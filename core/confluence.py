"""
===============================================================================
  Multi-Timeframe Confluence Engine
===============================================================================
  The alpha generator.  We only trade when multiple timeframes AGREE.
  Higher TF sets the bias → Trading TF finds the setup → Entry TF times it.
===============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from core.indicators import compute_all_indicators, determine_trend, trend_strength
from core.patterns import scan_candlestick_patterns, scan_chart_patterns
from core.structures import (
    find_sr_levels,
    find_supply_demand_zones,
    nearest_sr,
    price_near_level,
    classify_structure,
)
from core.smart_money import analyze_smart_money
from core.mt5_connector import MT5Connector
from utils.logger import get_logger
from utils import market_hours

log = get_logger("confluence")

# Timeframe hierarchy: higher → lower
TF_HIERARCHY = ["W1", "D1", "H4", "H1", "M15", "M5"]

# Which TFs serve which role
HIGHER_TFS = ["W1", "D1", "H4"]
TRADING_TFS = ["H1", "M15"]
ENTRY_TFS = ["M5", "M15"]


@dataclass
class TimeframeAnalysis:
    """Analysis results for a single timeframe."""
    symbol: str
    timeframe: str
    trend: str = "NEUTRAL"
    trend_strength: float = 0.0
    structure: str = "range"
    sr_levels: list = field(default_factory=list)
    sd_zones: list = field(default_factory=list)
    candle_patterns: list = field(default_factory=list)
    chart_patterns: list = field(default_factory=list)
    smart_money: dict = field(default_factory=dict)
    indicators: dict = field(default_factory=dict)
    df: Optional[pd.DataFrame] = None


@dataclass
class SymbolAnalysis:
    """Complete multi-timeframe analysis for a single symbol."""
    symbol: str
    timeframes: dict[str, TimeframeAnalysis] = field(default_factory=dict)
    higher_tf_bias: str = "NEUTRAL"
    trading_tf_bias: str = "NEUTRAL"
    entry_tf_bias: str = "NEUTRAL"
    overall_bias: str = "NEUTRAL"
    confluence_score: float = 0.0
    trade_direction: Optional[str] = None  # "BUY" or "SELL" or None
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    atr: float = 0.0
    spread_pips: float = 0.0


def analyze_timeframe(
    mt5: MT5Connector,
    symbol: str,
    timeframe: str,
) -> TimeframeAnalysis:
    """Run full analysis on a single symbol/timeframe pair."""
    tfa = TimeframeAnalysis(symbol=symbol, timeframe=timeframe)

    df = mt5.get_rates(symbol, timeframe)
    # FIXED: require enough bars for the slowest indicator (200-period EMA)
    min_bars = max(cfg.EMA_TREND + 50, cfg.ICHI_SENKOU_B + cfg.ICHI_KIJUN + 10, 100)
    if df is None or len(df) < min_bars:
        return tfa

    # Compute all indicators
    df = compute_all_indicators(df)
    tfa.df = df

    # Trend
    tfa.trend = determine_trend(df)
    tfa.trend_strength = trend_strength(df)

    # Structure
    tfa.structure = classify_structure(df)

    # S/R levels
    tfa.sr_levels = find_sr_levels(df)

    # Supply/demand zones
    tfa.sd_zones = find_supply_demand_zones(df)

    # Candlestick patterns
    tfa.candle_patterns = scan_candlestick_patterns(df)

    # Chart patterns
    tfa.chart_patterns = scan_chart_patterns(df)

    # Smart money concepts
    tfa.smart_money = analyze_smart_money(df)

    # Snapshot of key indicator values (last bar)
    last = df.iloc[-1]
    tfa.indicators = {
        "close": last["close"],
        "rsi": last.get("rsi", np.nan),
        "macd": last.get("macd", np.nan),
        "macd_hist": last.get("macd_hist", np.nan),
        "adx": last.get("adx", np.nan),
        "atr": last.get("atr", np.nan),
        "bb_pct_b": last.get("bb_pct_b", np.nan),
        "stoch_k": last.get("stoch_k", np.nan),
        "cci": last.get("cci", np.nan),
        "ema_fast": last.get("ema_fast", np.nan),
        "ema_trend": last.get("ema_trend", np.nan),
        "vol_ratio": last.get("vol_ratio", np.nan),
        "squeeze": last.get("squeeze", False),
        "rsi_divergence": last.get("rsi_divergence", 0),
        "macd_divergence": last.get("macd_divergence", 0),
    }

    return tfa


def analyze_symbol(mt5_conn: MT5Connector, symbol: str) -> SymbolAnalysis:
    """
    Run multi-timeframe analysis on a symbol.
    This is the core of the confluence engine.
    """
    sa = SymbolAnalysis(symbol=symbol)

    # Analyze each timeframe
    for tf in TF_HIERARCHY:
        tfa = analyze_timeframe(mt5_conn, symbol, tf)
        sa.timeframes[tf] = tfa

    # ── Determine higher-TF bias ─────────────────────────────────────────
    higher_biases = []
    for tf in HIGHER_TFS:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.trend != "NEUTRAL":
            weight = 3 if tf == "D1" else (2 if tf == "H4" else 1)
            val = 1 if tfa.trend == "BULLISH" else -1
            higher_biases.extend([val] * weight)

    if higher_biases:
        avg = sum(higher_biases) / len(higher_biases)
        sa.higher_tf_bias = "BULLISH" if avg > 0.2 else ("BEARISH" if avg < -0.2 else "NEUTRAL")

    # ── Determine trading-TF bias ────────────────────────────────────────
    trading_biases = []
    for tf in TRADING_TFS:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.trend != "NEUTRAL":
            val = 1 if tfa.trend == "BULLISH" else -1
            trading_biases.append(val)

    if trading_biases:
        avg = sum(trading_biases) / len(trading_biases)
        sa.trading_tf_bias = "BULLISH" if avg > 0 else ("BEARISH" if avg < 0 else "NEUTRAL")

    # ── Determine entry-TF bias ──────────────────────────────────────────
    entry_biases = []
    for tf in ENTRY_TFS:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.trend != "NEUTRAL":
            val = 1 if tfa.trend == "BULLISH" else -1
            entry_biases.append(val)

    if entry_biases:
        avg = sum(entry_biases) / len(entry_biases)
        sa.entry_tf_bias = "BULLISH" if avg > 0 else ("BEARISH" if avg < 0 else "NEUTRAL")

    # ── Overall bias (requires alignment) ────────────────────────────────
    if sa.higher_tf_bias == sa.trading_tf_bias and sa.higher_tf_bias != "NEUTRAL":
        sa.overall_bias = sa.higher_tf_bias
    else:
        sa.overall_bias = "NEUTRAL"

    # ── Trade direction ──────────────────────────────────────────────────
    if sa.overall_bias == "BULLISH" and sa.entry_tf_bias in ("BULLISH", "NEUTRAL"):
        sa.trade_direction = "BUY"
    elif sa.overall_bias == "BEARISH" and sa.entry_tf_bias in ("BEARISH", "NEUTRAL"):
        sa.trade_direction = "SELL"
    else:
        sa.trade_direction = None

    # ── ATR & spread ─────────────────────────────────────────────────────
    h1 = sa.timeframes.get("H1")
    if h1 and h1.indicators:
        atr_val = h1.indicators.get("atr", np.nan)
        sa.atr = atr_val if not np.isnan(atr_val) else 0.0

    sa.spread_pips = mt5_conn.spread_pips(symbol)

    # ── Compute SL / TP if we have a direction ───────────────────────────
    if sa.trade_direction and sa.atr > 0:
        entry_tf = sa.timeframes.get("M15") or sa.timeframes.get("H1")
        if entry_tf and entry_tf.df is not None:
            current_price = entry_tf.indicators.get("close", 0)
            sa.entry_price = current_price

            # Get broker's minimum stop level for this symbol
            sym_info = mt5_conn.symbol_info(symbol)
            min_stop_level = 0
            if sym_info:
                # stops_level is in points; convert to price distance
                stops_level = sym_info.get("trade_stops_level", 0)
                point = sym_info.get("point", 0.00001)
                min_stop_level = stops_level * point

            # Calculate initial SL/TP based on ATR
            if sa.trade_direction == "BUY":
                sa.stop_loss = current_price - cfg.ATR_SL_MULTIPLIER * sa.atr
                sa.take_profit = current_price + cfg.ATR_TP_MULTIPLIER * sa.atr
            else:
                sa.stop_loss = current_price + cfg.ATR_SL_MULTIPLIER * sa.atr
                sa.take_profit = current_price - cfg.ATR_TP_MULTIPLIER * sa.atr

            # Enforce broker's minimum stop level
            if min_stop_level > 0:
                if sa.trade_direction == "BUY":
                    min_sl = current_price - min_stop_level
                    min_tp = current_price + min_stop_level
                    if sa.stop_loss > min_sl:
                        sa.stop_loss = min_sl
                    if sa.take_profit < min_tp:
                        sa.take_profit = min_tp
                else:  # SELL
                    max_sl = current_price + min_stop_level
                    max_tp = current_price - min_stop_level
                    if sa.stop_loss < max_sl:
                        sa.stop_loss = max_sl
                    if sa.take_profit > max_tp:
                        sa.take_profit = max_tp

            # Adjust SL to be beyond nearest S/R level
            _adjust_sl_to_structure(sa, entry_tf)

    return sa


def _adjust_sl_to_structure(sa: SymbolAnalysis, tfa: TimeframeAnalysis):
    """
    Move the stop loss just beyond the nearest support/resistance level
    to avoid being stopped out at obvious levels (where everyone places SL).
    """
    if not tfa.sr_levels or sa.atr == 0:
        return

    price = sa.entry_price
    sr = nearest_sr(tfa.sr_levels, price, n=2)
    buffer = sa.atr * 0.3  # small buffer beyond the level

    if sa.trade_direction == "BUY" and sr["nearest_support"]:
        nearest_sup = sr["nearest_support"][0]["price"]
        proposed_sl = nearest_sup - buffer
        # Only use structure-based SL if it's tighter than ATR-based
        if proposed_sl > sa.stop_loss:
            sa.stop_loss = proposed_sl

    if sa.trade_direction == "SELL" and sr["nearest_resistance"]:
        nearest_res = sr["nearest_resistance"][0]["price"]
        proposed_sl = nearest_res + buffer
        if proposed_sl < sa.stop_loss:
            sa.stop_loss = proposed_sl


def compute_confluence_score(sa: SymbolAnalysis) -> float:
    """
    Compute the confidence/confluence score (0-100) for a potential trade.
    This is the gatekeeper — only trades scoring >= CONFIDENCE_THRESHOLD pass.
    """
    if sa.trade_direction is None:
        return 0.0

    weights = cfg.CONFIDENCE_WEIGHTS
    score = 0.0
    direction_val = 1 if sa.trade_direction == "BUY" else -1

    # ── 1. Multi-TF Alignment (0-20) ────────────────────────────────────
    tf_agreement = 0
    for tf in TF_HIERARCHY:
        tfa = sa.timeframes.get(tf)
        if tfa:
            if (tfa.trend == "BULLISH" and direction_val == 1) or \
               (tfa.trend == "BEARISH" and direction_val == -1):
                tf_agreement += 1

    alignment_pct = tf_agreement / len(TF_HIERARCHY)
    score += alignment_pct * weights["multi_tf_alignment"]

    # ── 2. Indicator Confluence (0-20) ──────────────────────────────────
    h1 = sa.timeframes.get("H1")
    if h1 and h1.indicators:
        ind = h1.indicators
        ind_agree = 0
        ind_total = 0

        # RSI
        rsi = ind.get("rsi")
        if rsi is not None and not np.isnan(rsi):
            ind_total += 1
            if direction_val == 1 and rsi < cfg.RSI_OVERBOUGHT and rsi > 40:
                ind_agree += 1
            elif direction_val == -1 and rsi > cfg.RSI_OVERSOLD and rsi < 60:
                ind_agree += 1

        # MACD histogram
        macd_h = ind.get("macd_hist")
        if macd_h is not None and not np.isnan(macd_h):
            ind_total += 1
            if (direction_val == 1 and macd_h > 0) or (direction_val == -1 and macd_h < 0):
                ind_agree += 1

        # EMA alignment
        ema_f = ind.get("ema_fast")
        ema_t = ind.get("ema_trend")
        if ema_f is not None and ema_t is not None and not np.isnan(ema_f) and not np.isnan(ema_t):
            ind_total += 1
            if (direction_val == 1 and ema_f > ema_t) or (direction_val == -1 and ema_f < ema_t):
                ind_agree += 1

        # ADX (trending)
        adx = ind.get("adx")
        if adx is not None and not np.isnan(adx) and adx > cfg.ADX_TREND_THRESHOLD:
            ind_total += 1
            ind_agree += 1  # trending market is good for directional trades

        # Stochastic — FIXED: "not overbought" is too weak for a BUY signal.
        # Require Stoch to be in the favourable half, not just "not extreme".
        stoch = ind.get("stoch_k")
        if stoch is not None and not np.isnan(stoch):
            ind_total += 1
            if direction_val == 1 and 20 < stoch < 70:
                ind_agree += 1  # BUY: Stoch rising from low-mid zone
            elif direction_val == -1 and 30 < stoch < 80:
                ind_agree += 1  # SELL: Stoch falling from mid-high zone

        # Divergence bonus
        rsi_div = ind.get("rsi_divergence", 0)
        macd_div = ind.get("macd_divergence", 0)
        if rsi_div == direction_val or macd_div == direction_val:
            ind_agree += 1
            ind_total += 1

        if ind_total > 0:
            score += (ind_agree / ind_total) * weights["indicator_confluence"]

    # ── 3. S/R Level Quality (0-15) ─────────────────────────────────────
    entry_tfa = sa.timeframes.get("H1") or sa.timeframes.get("M15")
    if entry_tfa and entry_tfa.sr_levels and sa.atr > 0:
        near_levels = price_near_level(
            sa.entry_price, entry_tfa.sr_levels, sa.atr, proximity_atr=2.0
        )
        if near_levels:
            best_level = near_levels[0]
            sr_strength = min(best_level.get("strength", 0), 1.0)  # clamp to [0, 1]
            # Buying near support or selling near resistance is ideal
            if direction_val == 1 and best_level.get("kind") in ("S", "SR"):
                score += sr_strength * weights["sr_level_quality"]
            elif direction_val == -1 and best_level.get("kind") in ("R", "SR"):
                score += sr_strength * weights["sr_level_quality"]

    # ── 4. Price Action Signal (0-15) ───────────────────────────────────
    pa_score = 0.0
    pa_budget = weights["price_action_signal"]

    # Candle patterns — best from any of the entry TFs (max 60% of budget)
    best_candle = 0.0
    for tf in ["M15", "H1", "M5"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.candle_patterns:
            for pat in tfa.candle_patterns:
                if pat["bias"] == direction_val:
                    best_candle = max(best_candle, pat["strength"])
                    break
    pa_score += best_candle * pa_budget * 0.6

    # Chart patterns bonus — best from higher TFs (max 40% of budget)
    best_chart = 0.0
    for tf in ["H1", "H4"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.chart_patterns:
            for pat in tfa.chart_patterns:
                if pat.get("bias") == direction_val:
                    best_chart = max(best_chart, pat.get("strength", 0))
                    break
    pa_score += best_chart * pa_budget * 0.4

    score += min(pa_score, pa_budget)  # hard cap at budget

    # ── 5. Volume Confirmation (0-10) ───────────────────────────────────
    # FIXED: Tightened thresholds.  1.2x average volume is barely
    # above noise.  Require 1.5x for full credit; 1.0x for partial.
    if h1 and h1.indicators:
        vol_ratio = h1.indicators.get("vol_ratio")
        if vol_ratio is not None and not np.isnan(vol_ratio):
            if vol_ratio > 1.5:
                score += weights["volume_confirmation"]
            elif vol_ratio > 1.0:
                score += weights["volume_confirmation"] * 0.5
            # Below-average volume → slight penalty (reduces score by up to 3)
            elif vol_ratio < 0.5:
                score -= min(3.0, weights["volume_confirmation"] * 0.3)

    # ── 6. Smart Money Pattern (0-10) ───────────────────────────────────
    sm_score = 0
    for tf in ["H1", "M15", "H4"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.smart_money:
            sm = tfa.smart_money
            if sm.get("overall_bias") == ("BULLISH" if direction_val == 1 else "BEARISH"):
                sm_score = max(sm_score, 0.7)
            # Bonus for specific patterns
            if sm.get("liquidity_sweeps"):
                for sweep in sm["liquidity_sweeps"]:
                    if sweep["bias"] == direction_val:
                        sm_score = max(sm_score, 0.9)
            if sm.get("structure_breaks"):
                for brk in sm["structure_breaks"]:
                    if "choch" in brk["type"] and brk["bias"] == direction_val:
                        sm_score = max(sm_score, 0.95)
    score += sm_score * weights["smart_money_pattern"]

    # ── 7. Market Session (0-5) ─────────────────────────────────────────
    session_sc = market_hours.session_score(sa.symbol)
    score += session_sc * weights["market_session"]

    # ── 8. Spread Quality (0-5) ─────────────────────────────────────────
    if sa.spread_pips <= 1.0:
        score += weights["spread_quality"]
    elif sa.spread_pips <= 2.0:
        score += weights["spread_quality"] * 0.8
    elif sa.spread_pips <= cfg.MAX_SPREAD_PIPS:
        score += weights["spread_quality"] * 0.4

    # ── 9. Spread-as-percent-of-risk penalty (NEW) ─────────────────────
    # If spread is a large fraction of the stop-loss distance,
    # the trade has terrible edge after costs.  Penalize heavily.
    if sa.atr > 0 and sa.entry_price > 0 and sa.stop_loss > 0:
        risk_distance = abs(sa.entry_price - sa.stop_loss)
        if risk_distance > 0:
            sym_info_point = 0.0001  # default for forex
            # Rough spread in price terms: spread_pips * pip_size
            spread_price = sa.spread_pips * sym_info_point
            spread_pct_of_risk = spread_price / risk_distance
            if spread_pct_of_risk > 0.15:  # spread > 15% of risk = bad
                penalty = min(10.0, spread_pct_of_risk * 30)
                score -= penalty

    sa.confluence_score = round(max(min(score, 100.0), 0.0), 2)
    return sa.confluence_score
