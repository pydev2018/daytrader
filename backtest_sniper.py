"""
Comprehensive backtest script for the M15 Sniper system.

This script simulates the event-driven M15 pipeline with:
- Fast pass → Deep pass (TPR/RBH/ECR)
- Pending/market entries
- M5-based position management
- Full reporting (trades, signals, equity curve, summary)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

import config as cfg
from core.mt5_connector import MT5Connector
from core.sniper.levels import (
    atr, ema, find_pivots, detect_range, atr_percentile,
    trend_state_from_pivots,
)
from core.sniper.tpr import detect_tpr_setup, check_tpr_trigger_on_close
from core.sniper.rbh import initialize_rbh_state, update_rbh_state
from core.sniper.ecr import evaluate_ecr
from core.sniper.state import SymbolState, ExecutionIntent
from utils.logger import setup_logging, get_logger
from utils import market_hours

log = get_logger("backtest")


@dataclass
class PendingOrder:
    symbol: str
    direction: str
    entry_type: str
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    expiry_time: datetime
    setup_type: str
    risk_factor: float
    atr: float
    created_time: datetime


@dataclass
class Position:
    symbol: str
    direction: str
    entry_time: datetime
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    setup_type: str
    size: float
    risk_factor: float
    atr: float
    partial_done: bool = False
    remaining_size: float = 0.0
    breakeven_set: bool = False
    closed: bool = False
    exit_time: Optional[datetime] = None
    exit_price: float = 0.0
    pnl: float = 0.0
    reason: str = ""


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_rates_range(symbol: str, timeframe: int, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    return df


def _calc_lot_size(mt5_conn: MT5Connector, symbol: str, direction: str,
                   entry_price: float, stop_loss: float, balance: float,
                   risk_factor: float) -> float:
    sym_info = mt5_conn.symbol_info(symbol)
    if sym_info is None:
        return 0.0
    vol_min = sym_info.get("volume_min", 0.01)
    vol_max = sym_info.get("volume_max", 100.0)
    vol_step = sym_info.get("volume_step", 0.01)
    tick_value = sym_info.get("trade_tick_value", 1.0)
    tick_size = sym_info.get("trade_tick_size", sym_info.get("point", 0.00001))

    risk_pct = cfg.MAX_RISK_PER_TRADE_PCT * risk_factor
    dollar_risk = balance * (risk_pct / 100)
    sl_dist = abs(entry_price - stop_loss)
    if sl_dist <= 0 or tick_size <= 0:
        return 0.0

    action = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    test_profit = mt5_conn.calc_profit(
        action, symbol, 1.0, entry_price,
        entry_price + sl_dist if direction == "BUY" else entry_price - sl_dist,
    )
    if test_profit is None or test_profit <= 0:
        profit_per_lot = (sl_dist / tick_size) * tick_value
    else:
        profit_per_lot = test_profit

    if profit_per_lot <= 0:
        return 0.0
    raw_lots = dollar_risk / profit_per_lot
    if vol_step > 0:
        lots = max(vol_min, int(raw_lots / vol_step) * vol_step)
    else:
        lots = max(vol_min, round(raw_lots, 2))
    lots = min(lots, vol_max)
    return round(lots, 2)


def _asset_class(symbol: str) -> str:
    base = symbol.upper().split(".")[0]
    if market_hours.is_crypto_symbol(symbol):
        return "crypto"
    if any(base.startswith(p) for p in cfg.SNIPER_METALS_PREFIXES):
        return "metals"
    if any(k in base for k in cfg.SNIPER_INDEX_KEYWORDS):
        return "indices"
    if any(k in base for k in cfg.SNIPER_COMMODITY_KEYWORDS):
        return "commodities"
    return "fx"


def _asset_profile(symbol: str) -> dict:
    profile = {
        "min_stop_atr": cfg.SNIPER_MIN_STOP_ATR,
        "no_chase_atr": cfg.SNIPER_NO_CHASE_ATR,
        "max_spread_atr": cfg.SNIPER_MAX_SPREAD_ATR,
        "tpr_rejection_enabled": cfg.TPR_REJECTION_ENABLED,
        "tpr_trigger_body_atr": cfg.TPR_TRIGGER_BODY_ATR,
        "rbh_break_body_atr": cfg.RBH_BREAK_BODY_ATR,
        "regime_min_conf": cfg.SNIPER_REGIME_MIN_CONF,
        "compression_max_pct": cfg.SNIPER_COMPRESSION_MAX_PCT,
        "ecr_enabled": True,
        "ecr_min_score": cfg.ECR_MIN_SCORE,
        "ecr_max_target_atr": cfg.ECR_MAX_TARGET_ATR,
        "ecr_max_spread_atr": cfg.ECR_MAX_SPREAD_ATR,
        "ecr_entry_body_atr": cfg.ECR_ENTRY_BODY_ATR,
        "ecr_max_ema50_slope_atr": cfg.ECR_MAX_EMA50_SLOPE_ATR,
        "ecr_risk_factor": cfg.ECR_RISK_FACTOR,
    }
    asset_class = _asset_class(symbol)
    overrides = cfg.SNIPER_ASSET_CLASS_OVERRIDES.get(asset_class, {})
    profile.update(overrides)
    return profile


def _effective_regime_min_conf(base_min_conf: float, adaptive_relax: float) -> float:
    if not cfg.SNIPER_ADAPTIVE_ENABLED:
        return base_min_conf
    return max(cfg.SNIPER_REGIME_MIN_CONF_RELAX, base_min_conf - adaptive_relax)


def _effective_compression_max(base_max: int, adaptive_relax: float) -> int:
    if not cfg.SNIPER_ADAPTIVE_ENABLED:
        return base_max
    if cfg.SNIPER_ADAPTIVE_MAX_RELAX <= 0:
        return base_max
    delta = cfg.SNIPER_COMPRESSION_RELAX_MAX_PCT - cfg.SNIPER_COMPRESSION_MAX_PCT
    ratio = min(1.0, adaptive_relax / cfg.SNIPER_ADAPTIVE_MAX_RELAX)
    return int(base_max + delta * ratio)


def _regime_scores(snapshot, compression_max: int) -> tuple[str, float, float, float]:
    atr_val = snapshot["atr"]
    if atr_val <= 0:
        return "transition", 0.0, 0.0, 0.0

    pivot_score = 1.0 if snapshot["trend_state"] == "trend" else 0.3
    ema_sep = abs(snapshot["ema20"] - snapshot["ema50"]) / atr_val
    ema_slope = abs(snapshot["ema20_slope"]) / atr_val if atr_val > 0 else 0.0
    ema_score = min(1.0, max(ema_sep, ema_slope) / 0.2)
    comp_trend_score = min(1.0, snapshot["compression_pct"] / 60.0)
    trend_conf = (pivot_score + ema_score + comp_trend_score) / 3.0

    width_score = 1.0 if snapshot["range_width"] >= cfg.RBH_RANGE_WIDTH_ATR * atr_val else 0.4
    touch_score = min(1.0, max(snapshot["touch_high"], snapshot["touch_low"]) / 3.0)
    comp_range_score = 1.0 if snapshot["compression_pct"] <= compression_max else 0.4
    range_conf = (width_score + touch_score + comp_range_score) / 3.0

    if trend_conf >= range_conf:
        return "trend", max(trend_conf, range_conf), trend_conf, range_conf
    return "range", max(trend_conf, range_conf), trend_conf, range_conf


def _simulate_position_on_bar(pos: Position, bar: pd.Series, balance: float, mt5_conn: MT5Connector) -> float:
    """
    Simulate position updates on a single M5 bar.
    Returns updated balance if closed/partial.
    """
    if pos.closed:
        return balance
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar["close"])

    # Conservative ordering: SL first if both touched
    if pos.direction == "BUY":
        if low <= pos.sl:
            exit_price = pos.sl
            pos.closed = True
            pos.exit_price = exit_price
            pos.exit_time = bar.name
            pos.reason = "SL"
        elif high >= pos.tp2 and pos.tp2 > 0:
            exit_price = pos.tp2
            pos.closed = True
            pos.exit_price = exit_price
            pos.exit_time = bar.name
            pos.reason = "TP2"
        elif high >= pos.tp1 and pos.tp1 > 0 and not pos.partial_done:
            size_close = pos.size * 0.5
            pnl_partial = mt5_conn.calc_profit(
                mt5.ORDER_TYPE_BUY, pos.symbol, size_close, pos.entry_price, pos.tp1
            ) or 0.0
            balance += pnl_partial
            pos.pnl += pnl_partial
            pos.partial_done = True
            pos.remaining_size = pos.size - size_close
    else:
        if high >= pos.sl:
            exit_price = pos.sl
            pos.closed = True
            pos.exit_price = exit_price
            pos.exit_time = bar.name
            pos.reason = "SL"
        elif low <= pos.tp2 and pos.tp2 > 0:
            exit_price = pos.tp2
            pos.closed = True
            pos.exit_price = exit_price
            pos.exit_time = bar.name
            pos.reason = "TP2"
        elif low <= pos.tp1 and pos.tp1 > 0 and not pos.partial_done:
            size_close = pos.size * 0.5
            pnl_partial = mt5_conn.calc_profit(
                mt5.ORDER_TYPE_SELL, pos.symbol, size_close, pos.entry_price, pos.tp1
            ) or 0.0
            balance += pnl_partial
            pos.pnl += pnl_partial
            pos.partial_done = True
            pos.remaining_size = pos.size - size_close

    if pos.closed:
        pnl = mt5_conn.calc_profit(
            mt5.ORDER_TYPE_BUY if pos.direction == "BUY" else mt5.ORDER_TYPE_SELL,
            pos.symbol,
            pos.size if not pos.partial_done else max(pos.remaining_size, pos.size * 0.5),
            pos.entry_price,
            pos.exit_price,
        ) or 0.0
        pos.pnl += pnl
        balance += pnl

    # Update breakeven and trailing AFTER bar closes (conservative)
    if not pos.closed:
        risk = abs(pos.entry_price - pos.sl) if pos.sl > 0 else 0
        if risk > 0:
            if pos.direction == "BUY":
                current_r = (close - pos.entry_price) / risk
            else:
                current_r = (pos.entry_price - close) / risk
            if current_r >= 1.0 and not pos.breakeven_set:
                pos.sl = pos.entry_price
                pos.breakeven_set = True
            if current_r >= 1.5:
                atr_val = float(bar.get("atr", 0)) if "atr" in bar else 0.0
                trail = atr_val if atr_val > 0 else risk * 0.5
                if pos.direction == "BUY":
                    pos.sl = max(pos.sl, close - trail)
                else:
                    pos.sl = min(pos.sl, close + trail)

    return balance


def _resolve_symbol_name(symbol: str) -> tuple[str, bool]:
    info = mt5.symbol_info(symbol)
    if info is not None:
        return symbol, False
    matches = mt5.symbols_get(f"{symbol}*")
    if not matches:
        return symbol, False
    names = [m.name for m in matches if m.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL]
    if len(names) == 1:
        return names[0], True
    return symbol, False


def run_backtest(args):
    setup_logging()
    mt5_conn = MT5Connector()
    if not mt5_conn.connect():
        raise RuntimeError("MT5 connection failed")

    start = _parse_dt(args.start)
    end = _parse_dt(args.end)
    if end <= start:
        raise ValueError("End date must be after start date")

    symbols: list[str] = []
    if args.symbols:
        raw = [s.strip() for s in args.symbols.split(",") if s.strip()]
        for sym in raw:
            resolved, changed = _resolve_symbol_name(sym)
            if changed:
                log.info(f"Resolved symbol '{sym}' → '{resolved}'")
            symbols.append(resolved)
    else:
        symbols = mt5_conn.get_symbols_by_groups()

    if not symbols:
        raise ValueError("No symbols found for backtest")

    ref_symbol = args.reference_symbol or symbols[0]

    # Load data
    m15_data = {}
    m5_data = {}
    sym_info = {}
    missing: list[str] = []
    for sym in symbols:
        info = mt5_conn.symbol_info(sym)
        if info:
            sym_info[sym] = info
        df_m15 = _load_rates_range(sym, mt5.TIMEFRAME_M15, start, end)
        df_m5 = _load_rates_range(sym, mt5.TIMEFRAME_M5, start, end)
        if df_m15 is None or df_m5 is None:
            missing.append(sym)
            continue
        df_m15["atr"] = atr(df_m15, period=14)
        df_m15["ema20"] = ema(df_m15["close"], 20)
        df_m15["ema50"] = ema(df_m15["close"], 50)
        df_m5["atr"] = atr(df_m5, period=14)
        m15_data[sym] = df_m15
        m5_data[sym] = df_m5

    if not m15_data:
        log.error("No historical data loaded. Check symbol names/suffixes and date range.")
        if missing:
            log.error(f"Missing data for: {', '.join(missing[:10])}")
        return
    if ref_symbol not in m15_data:
        ref_symbol = list(m15_data.keys())[0]

    ref_times = m15_data[ref_symbol].index
    ref_times = ref_times[ref_times >= start]

    states: dict[str, SymbolState] = {sym: SymbolState(symbol=sym) for sym in m15_data.keys()}
    positions: list[Position] = []
    pending_orders: list[PendingOrder] = []
    trade_records: list[dict] = []
    signal_records: list[dict] = []
    reject_records: list[dict] = []
    recorded_ids: set[int] = set()
    equity_curve: list[dict] = []

    balance = args.capital if args.capital > 0 else cfg.TRADING_CAPITAL
    peak_balance = balance
    max_dd = 0.0
    adaptive_relax = 0.0
    last_signal_bar = 0

    for i in range(60, len(ref_times) - 1):
        bar_time = ref_times[i]
        next_time = ref_times[i + 1]
        bar_seq = int(bar_time.timestamp() // (15 * 60))

        # Adaptive gating
        if cfg.SNIPER_ADAPTIVE_ENABLED and last_signal_bar:
            idle_bars = max(0, bar_seq - last_signal_bar)
            if idle_bars >= cfg.SNIPER_ADAPTIVE_IDLE_BARS:
                adaptive_relax = min(
                    cfg.SNIPER_ADAPTIVE_MAX_RELAX,
                    adaptive_relax + cfg.SNIPER_ADAPTIVE_RELAX_STEP,
                )

        # Fast pass
        candidates = []
        for sym, df_m15 in m15_data.items():
            if bar_time not in df_m15.index:
                continue
            if not market_hours.is_good_session_for_symbol(sym, now=bar_time):
                continue
            closed = df_m15.loc[:bar_time].copy()
            if len(closed) < cfg.SNIPER_FAST_PASS_BARS:
                continue
            atr_val = float(closed["atr"].iloc[-1]) if not np.isnan(closed["atr"].iloc[-1]) else 0.0
            if atr_val <= 0:
                continue
            ema20_val = float(closed["ema20"].iloc[-1])
            ema50_val = float(closed["ema50"].iloc[-1])
            ema20_slope = float(ema20_val - closed["ema20"].iloc[-5]) if len(closed) > 6 else 0.0
            pivots = find_pivots(closed, cfg.SNIPER_PIVOT_L)
            trend_state = trend_state_from_pivots(pivots) if pivots else "transition"

            range_info = detect_range(pivots, atr_val, cfg.SNIPER_RANGE_LOOKBACK_BARS, cfg.RBH_TOUCH_TOL_ATR)
            compression_pct = atr_percentile(closed["atr"], cfg.SNIPER_COMPRESSION_BARS)
            point = sym_info.get(sym, {}).get("point", 0.00001)
            spread = float(closed["spread"].iloc[-1]) * point if "spread" in closed.columns else 0.0
            spread_atr_ratio = spread / atr_val if atr_val > 0 else 0.0

            snapshot = {
                "atr": atr_val,
                "ema20": ema20_val,
                "ema50": ema50_val,
                "ema20_slope": ema20_slope,
                "trend_state": trend_state,
                "range_width": range_info.width,
                "touch_high": range_info.touch_high,
                "touch_low": range_info.touch_low,
                "compression_pct": compression_pct,
            }

            profile = _asset_profile(sym)
            compression_max = _effective_compression_max(
                profile.get("compression_max_pct", cfg.SNIPER_COMPRESSION_MAX_PCT),
                adaptive_relax,
            )
            regime_min_conf = _effective_regime_min_conf(
                profile.get("regime_min_conf", cfg.SNIPER_REGIME_MIN_CONF),
                adaptive_relax,
            )
            base_regime, regime_conf, trend_conf, range_conf = _regime_scores(snapshot, compression_max)
            if regime_conf < regime_min_conf:
                regime = "transition"
            else:
                regime = base_regime

            if spread_atr_ratio > profile.get("max_spread_atr", cfg.SNIPER_MAX_SPREAD_ATR):
                continue

            quick_score = 40.0 + (25.0 if regime == "trend" else 20.0) + 15.0
            candidates.append((sym, regime, quick_score, closed, snapshot, trend_conf, range_conf, profile))

        candidates.sort(key=lambda x: x[2], reverse=True)
        shortlist = candidates[:cfg.SNIPER_SHORTLIST_MAX]

        # Deep pass
        intents: list[ExecutionIntent] = []
        for sym, regime, _, closed, snapshot, trend_conf, _, profile in shortlist:
            state = states[sym]
            # Hysteresis
            if state.regime == regime:
                state.regime_streak += 1
            else:
                state.regime = regime
                state.regime_streak = 1
            regime_confirmed = state.regime_streak >= cfg.SNIPER_REGIME_HYSTERESIS_BARS
            use_regime = state.regime if regime_confirmed else "transition"

            atr_val = snapshot["atr"]
            pivots = find_pivots(closed, cfg.SNIPER_PIVOT_L)
            ema20_val = snapshot["ema20"]
            ema50_val = snapshot["ema50"]
            spread_atr_ratio = 0.0
            if atr_val > 0:
                point = sym_info.get(sym, {}).get("point", 0.00001)
                spread = float(closed["spread"].iloc[-1]) * point if "spread" in closed.columns else 0.0
                spread_atr_ratio = spread / atr_val

            execution_style = args.execution_style or cfg.SNIPER_EXECUTION_STYLE

            if use_regime == "trend":
                tpr_state = detect_tpr_setup(
                    sym,
                    closed,
                    pivots,
                    ema20_val,
                    ema50_val,
                    atr_val,
                    spread_atr_ratio,
                    bar_seq,
                    params=profile,
                )
                if tpr_state:
                    closed_bar = closed.iloc[-1]
                    prev_bar = closed.iloc[-2]
                    trigger = check_tpr_trigger_on_close(
                        tpr_state,
                        closed_bar,
                        prev_bar,
                        atr_val,
                        ema20_val,
                        params=profile,
                    )
                    if trigger:
                        is_rejection = "REJECTION" in trigger.reasons
                        use_pending = execution_style in ("pending", "hybrid") and not is_rejection
                        entry_type = "market" if is_rejection else ("pending_stop" if use_pending else "market")
                        entry_price = trigger.trigger_price if is_rejection else tpr_state.trigger_level
                        intents.append(ExecutionIntent(
                            setup_type="TPR",
                            symbol=sym,
                            direction=trigger.direction,
                            entry_type=entry_type,
                            entry_price=entry_price,
                            sl=tpr_state.sl,
                            tp1=tpr_state.tp1,
                            tp2=tpr_state.tp2,
                            expiry_bar=cfg.SNIPER_PENDING_EXPIRY_BARS,
                            risk_factor=min(1.0, tpr_state.confidence / 100),
                            atr=tpr_state.atr,
                            trigger_level=tpr_state.trigger_level,
                            confidence=tpr_state.confidence,
                            reasons=trigger.reasons,
                        ))
            elif use_regime == "range":
                rbh_state = initialize_rbh_state(
                    sym, closed, pivots, atr_val, spread_atr_ratio, bar_seq, params=profile
                )
                if rbh_state:
                    rbh_state, trigger = update_rbh_state(
                        rbh_state, closed, atr_val, bar_seq, params=profile
                    )
                    if trigger:
                        use_pending = execution_style in ("pending", "hybrid")
                        entry_type = "pending_limit" if use_pending else "market"
                        entry_price = rbh_state.entry if use_pending else rbh_state.entry
                        intents.append(ExecutionIntent(
                            setup_type="RBH",
                            symbol=sym,
                            direction=trigger.direction,
                            entry_type=entry_type,
                            entry_price=entry_price,
                            sl=rbh_state.sl,
                            tp1=rbh_state.tp1,
                            tp2=rbh_state.tp2,
                            expiry_bar=cfg.SNIPER_PENDING_EXPIRY_BARS,
                            risk_factor=min(1.0, rbh_state.confidence / 100),
                            atr=rbh_state.atr,
                            trigger_level=rbh_state.entry,
                            confidence=rbh_state.confidence,
                            reasons=trigger.reasons,
                        ))
            else:
                if not profile.get("ecr_enabled", True):
                    continue
                if trend_conf >= cfg.ECR_TREND_VETO_CONF:
                    continue
                if cfg.ECR_SESSION_ONLY and not market_hours.is_crypto_symbol(sym):
                    sessions = market_hours.active_sessions(now=bar_time)
                    if not (set(sessions) & set(cfg.ECR_ALLOWED_SESSIONS)):
                        continue
                ecr_state, trigger = evaluate_ecr(
                    sym, closed, atr_val, spread_atr_ratio, bar_seq, "transition", params=profile
                )
                if trigger and ecr_state:
                    use_pending = execution_style in ("pending", "hybrid")
                    entry_type = "pending_limit" if use_pending else "market"
                    entry_price = ecr_state.trigger_level if use_pending else ecr_state.entry_price
                    intents.append(ExecutionIntent(
                        setup_type="ECR",
                        symbol=sym,
                        direction=trigger.direction,
                        entry_type=entry_type,
                        entry_price=entry_price,
                        sl=ecr_state.sl,
                        tp1=ecr_state.tp1,
                        tp2=ecr_state.tp2,
                        expiry_bar=2,
                        risk_factor=min(1.0, ecr_state.confidence / 100)
                        * profile.get("ecr_risk_factor", cfg.ECR_RISK_FACTOR),
                        atr=ecr_state.atr,
                        trigger_level=ecr_state.trigger_level,
                        confidence=ecr_state.confidence,
                        reasons=trigger.reasons,
                    ))

        if intents:
            adaptive_relax = 0.0
            last_signal_bar = bar_seq

        # Record signals
        for intent in intents:
            signal_records.append({
                "time": bar_time.isoformat(),
                "symbol": intent.symbol,
                "setup": intent.setup_type,
                "direction": intent.direction,
                "entry_type": intent.entry_type,
                "entry_price": intent.entry_price,
                "sl": intent.sl,
                "tp1": intent.tp1,
                "tp2": intent.tp2,
                "confidence": intent.confidence,
                "reasons": "|".join(intent.reasons),
            })

        # Place orders
        for intent in intents:
            if len([p for p in positions if not p.closed]) >= cfg.MAX_CONCURRENT_POSITIONS:
                reject_records.append({
                    "time": bar_time.isoformat(),
                    "symbol": intent.symbol,
                    "setup": intent.setup_type,
                    "reason": "max_positions",
                })
                continue
            lot_size = _calc_lot_size(
                mt5_conn, intent.symbol, intent.direction,
                intent.entry_price, intent.sl, balance, intent.risk_factor
            )
            if lot_size <= 0:
                reject_records.append({
                    "time": bar_time.isoformat(),
                    "symbol": intent.symbol,
                    "setup": intent.setup_type,
                    "reason": "size_zero",
                })
                continue

            if intent.entry_type in ("pending_stop", "pending_limit"):
                expiry_time = bar_time + timedelta(minutes=15 * intent.expiry_bar)
                pending_orders.append(PendingOrder(
                    symbol=intent.symbol,
                    direction=intent.direction,
                    entry_type=intent.entry_type,
                    entry_price=intent.entry_price,
                    sl=intent.sl,
                    tp1=intent.tp1,
                    tp2=intent.tp2,
                    expiry_time=expiry_time,
                    setup_type=intent.setup_type,
                    risk_factor=intent.risk_factor,
                    atr=intent.atr,
                    created_time=bar_time,
                ))
            else:
                pos = Position(
                    symbol=intent.symbol,
                    direction=intent.direction,
                    entry_time=bar_time,
                    entry_price=intent.entry_price,
                    sl=intent.sl,
                    tp1=intent.tp1,
                    tp2=intent.tp2,
                    setup_type=intent.setup_type,
                    size=lot_size,
                    risk_factor=intent.risk_factor,
                    atr=intent.atr,
                    remaining_size=lot_size,
                )
                positions.append(pos)

        # Simulate M5 bars between bar_time and next_time
        for sym, df_m5 in m5_data.items():
            m5_slice = df_m5[(df_m5.index > bar_time) & (df_m5.index <= next_time)]
            if m5_slice.empty:
                continue
            for _, bar in m5_slice.iterrows():
                bar_time_m5 = bar.name

                # Expire pending orders
                expired = [o for o in pending_orders if o.expiry_time <= bar_time_m5]
                for o in expired:
                    reject_records.append({
                        "time": bar_time_m5.isoformat(),
                        "symbol": o.symbol,
                        "setup": o.setup_type,
                        "reason": "pending_expired",
                    })
                pending_orders[:] = [o for o in pending_orders if o.expiry_time > bar_time_m5]

                # Pending order fills
                for order in [o for o in pending_orders if o.symbol == sym]:
                    high = float(bar["high"])
                    low = float(bar["low"])
                    filled = False
                    if order.entry_type == "pending_stop":
                        if order.direction == "BUY" and high >= order.entry_price:
                            filled = True
                        if order.direction == "SELL" and low <= order.entry_price:
                            filled = True
                    else:
                        if order.direction == "BUY" and low <= order.entry_price:
                            filled = True
                        if order.direction == "SELL" and high >= order.entry_price:
                            filled = True
                    if filled and len([p for p in positions if not p.closed]) < cfg.MAX_CONCURRENT_POSITIONS:
                        lot_size = _calc_lot_size(
                            mt5_conn, order.symbol, order.direction,
                            order.entry_price, order.sl, balance, order.risk_factor
                        )
                        if lot_size > 0:
                            pos = Position(
                                symbol=order.symbol,
                                direction=order.direction,
                                entry_time=bar_time_m5,
                                entry_price=order.entry_price,
                                sl=order.sl,
                                tp1=order.tp1,
                                tp2=order.tp2,
                                setup_type=order.setup_type,
                                size=lot_size,
                                risk_factor=order.risk_factor,
                                atr=order.atr,
                                remaining_size=lot_size,
                            )
                            positions.append(pos)
                        pending_orders.remove(order)

                for pos in [p for p in positions if p.symbol == sym and not p.closed]:
                    balance = _simulate_position_on_bar(pos, bar, balance, mt5_conn)

        # Record closed positions
        for pos in [p for p in positions if p.closed and p.exit_time]:
            if id(pos) in recorded_ids:
                continue
            trade_records.append({
                "id": id(pos),
                "symbol": pos.symbol,
                "setup": pos.setup_type,
                "direction": pos.direction,
                "entry_time": pos.entry_time.isoformat(),
                "entry_price": pos.entry_price,
                "exit_time": pos.exit_time.isoformat() if pos.exit_time else "",
                "exit_price": pos.exit_price,
                "pnl": pos.pnl,
                "reason": pos.reason,
                "size": pos.size,
            })
            recorded_ids.add(id(pos))

        # Equity curve
        peak_balance = max(peak_balance, balance)
        dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
        max_dd = max(max_dd, dd)
        equity_curve.append({"time": bar_time.isoformat(), "balance": balance})

    # Force-close remaining open positions at last M5 close
    for pos in [p for p in positions if not p.closed]:
        df_m5 = m5_data.get(pos.symbol)
        if df_m5 is None or df_m5.empty:
            continue
        last_bar = df_m5.iloc[-1]
        exit_price = float(last_bar["close"])
        pos.exit_price = exit_price
        pos.exit_time = df_m5.index[-1]
        pos.closed = True
        pos.reason = "EOD"
        pnl = mt5_conn.calc_profit(
            mt5.ORDER_TYPE_BUY if pos.direction == "BUY" else mt5.ORDER_TYPE_SELL,
            pos.symbol,
            pos.size if not pos.partial_done else max(pos.remaining_size, pos.size * 0.5),
            pos.entry_price,
            exit_price,
        ) or 0.0
        pos.pnl += pnl
        balance += pnl
        trade_records.append({
            "id": id(pos),
            "symbol": pos.symbol,
            "setup": pos.setup_type,
            "direction": pos.direction,
            "entry_time": pos.entry_time.isoformat(),
            "entry_price": pos.entry_price,
            "exit_time": pos.exit_time.isoformat() if pos.exit_time else "",
            "exit_price": pos.exit_price,
            "pnl": pos.pnl,
            "reason": pos.reason,
            "size": pos.size,
        })

    # Summary
    wins = [t for t in trade_records if t["pnl"] > 0]
    losses = [t for t in trade_records if t["pnl"] <= 0]
    by_setup = {}
    for t in trade_records:
        by_setup.setdefault(t["setup"], []).append(t)
    setup_stats = {}
    for setup, trades in by_setup.items():
        swins = [t for t in trades if t["pnl"] > 0]
        sloss = [t for t in trades if t["pnl"] <= 0]
        gp = sum(t["pnl"] for t in swins)
        gl = abs(sum(t["pnl"] for t in sloss))
        setup_stats[setup] = {
            "trades": len(trades),
            "wins": len(swins),
            "losses": len(sloss),
            "win_rate": (len(swins) / len(trades)) if trades else 0.0,
            "net_pnl": gp - gl,
            "profit_factor": gp / gl if gl > 0 else 0.0,
        }
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": len(m15_data),
        "trades": len(trade_records),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trade_records)) if trade_records else 0.0,
        "net_pnl": gross_profit - gross_loss,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_dd * 100,
        "final_balance": balance,
        "setup_stats": setup_stats,
        "rejections": len(reject_records),
    }

    # Output
    out_dir = args.output_dir or os.path.join("data", "backtests", f"sniper_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with open(os.path.join(out_dir, "trades.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=trade_records[0].keys() if trade_records else [])
        if trade_records:
            writer.writeheader()
            writer.writerows(trade_records)
    with open(os.path.join(out_dir, "signals.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=signal_records[0].keys() if signal_records else [])
        if signal_records:
            writer.writeheader()
            writer.writerows(signal_records)
    with open(os.path.join(out_dir, "rejections.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=reject_records[0].keys() if reject_records else [])
        if reject_records:
            writer.writeheader()
            writer.writerows(reject_records)
    with open(os.path.join(out_dir, "equity_curve.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "balance"])
        writer.writeheader()
        writer.writerows(equity_curve)

    log.info(f"Backtest complete → {out_dir}")
    log.info(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description="M15 Sniper Backtest")
    parser.add_argument("--start", required=True, help="Start datetime (YYYY-MM-DD or ISO)")
    parser.add_argument("--end", required=True, help="End datetime (YYYY-MM-DD or ISO)")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols (optional)")
    parser.add_argument("--reference-symbol", default="", help="Reference symbol for bar timeline")
    parser.add_argument("--output-dir", default="", help="Output directory")
    parser.add_argument("--capital", type=float, default=0.0, help="Starting capital (default TRADING_CAPITAL)")
    parser.add_argument("--execution-style", default="", help="Override execution style (market_close|market_intrabar|pending|hybrid)")
    args = parser.parse_args()
    run_backtest(args)


if __name__ == "__main__":
    main()
