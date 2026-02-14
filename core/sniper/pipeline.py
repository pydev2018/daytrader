"""
M15 Sniper pipeline: fast pass, deep pass, intrabar monitoring.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as cfg
from core.mt5_connector import MT5Connector
from utils.logger import get_logger
from utils import market_hours
from .levels import atr, ema, find_pivots, trend_state_from_pivots, detect_range, atr_percentile, major_levels_from_pivots
from .state import M15Snapshot, FastCandidate, SymbolState, ExecutionIntent
from .tpr import detect_tpr_setup, check_tpr_trigger_on_close, check_tpr_trigger_intrabar
from .rbh import initialize_rbh_state, update_rbh_state
from .ecr import evaluate_ecr


log = get_logger("sniper")

_M15_SECONDS = 15 * 60


class SniperPipeline:
    """Event-driven M15 sniper pipeline."""

    def __init__(self, mt5_conn: MT5Connector):
        self.mt5 = mt5_conn
        self._universe: list[str] = []
        self._states: dict[str, SymbolState] = {}
        self._intrabar_symbols: list[str] = []
        self._last_signal_bar: int = 0
        self._adaptive_relax: float = 0.0

    def refresh_universe(self):
        self._universe = self.mt5.get_symbols_by_groups()
        log.info(f"[SNIPER] Universe refreshed: {len(self._universe)} symbols")

    def _asset_class(self, symbol: str) -> str:
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

    def _asset_profile(self, symbol: str) -> dict:
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
        asset_class = self._asset_class(symbol)
        overrides = cfg.SNIPER_ASSET_CLASS_OVERRIDES.get(asset_class, {})
        profile.update(overrides)
        return profile

    @property
    def universe(self) -> list[str]:
        return self._universe

    def get_forming_bar_time(self, symbol: str) -> int:
        _, forming_time = self._get_closed_m15(symbol, 3)
        return forming_time

    def _get_closed_m15(self, symbol: str, count: int) -> tuple[pd.DataFrame | None, int]:
        df = self.mt5.get_rates(symbol, "M15", count=count)
        if df is None or len(df) < 30:
            return None, 0
        forming_time = int(df.index[-1].timestamp())
        closed = df.iloc[:-1].copy()
        return closed, forming_time

    def _build_snapshot(self, symbol: str, closed: pd.DataFrame, forming_time: int) -> Optional[M15Snapshot]:
        if closed is None or len(closed) < 30:
            return None
        closed = closed.copy()
        closed["atr"] = atr(closed, period=14)
        closed["ema20"] = ema(closed["close"], 20)
        closed["ema50"] = ema(closed["close"], 50)

        atr_val = float(closed["atr"].iloc[-1]) if not np.isnan(closed["atr"].iloc[-1]) else 0.0
        ema20_val = float(closed["ema20"].iloc[-1])
        ema50_val = float(closed["ema50"].iloc[-1])
        ema20_slope = float(ema20_val - closed["ema20"].iloc[-5]) if len(closed) > 6 else 0.0
        ema50_slope = float(ema50_val - closed["ema50"].iloc[-5]) if len(closed) > 6 else 0.0

        pivots = find_pivots(closed, cfg.SNIPER_PIVOT_L)
        trend_state = trend_state_from_pivots(pivots)

        range_info = detect_range(pivots, atr_val, cfg.SNIPER_RANGE_LOOKBACK_BARS, cfg.RBH_TOUCH_TOL_ATR)
        compression = atr_percentile(closed["atr"], cfg.SNIPER_COMPRESSION_BARS)

        # Spread in price units
        spread_pips = self.mt5.spread_pips(symbol)
        tick = self.mt5.symbol_tick(symbol)
        spread_price = 0.0
        if tick:
            spread_price = tick["ask"] - tick["bid"]
        spread_atr_ratio = spread_price / atr_val if atr_val > 0 else 0.0

        major_pivots = find_pivots(closed.tail(cfg.SNIPER_MAJOR_LEVEL_BARS), cfg.SNIPER_PIVOT_L)
        major_levels = major_levels_from_pivots(major_pivots, atr_val)

        return M15Snapshot(
            symbol=symbol,
            bars_count=len(closed),
            forming_bar_time=forming_time,
            atr14=atr_val,
            ema20=ema20_val,
            ema50=ema50_val,
            ema20_slope=ema20_slope,
            ema50_slope=ema50_slope,
            pivots=pivots,
            trend_state=trend_state,
            range_high=range_info.range_high,
            range_low=range_info.range_low,
            range_width=range_info.width,
            touch_count_high=range_info.touch_high,
            touch_count_low=range_info.touch_low,
            compression_pct=compression,
            major_levels=major_levels,
            spread_pips=spread_pips,
            spread_atr_ratio=spread_atr_ratio,
        )

    def _effective_regime_min_conf(self, base_min_conf: float) -> float:
        if not cfg.SNIPER_ADAPTIVE_ENABLED:
            return base_min_conf
        return max(
            cfg.SNIPER_REGIME_MIN_CONF_RELAX,
            base_min_conf - self._adaptive_relax,
        )

    def _effective_compression_max(self, base_max: int) -> int:
        if not cfg.SNIPER_ADAPTIVE_ENABLED:
            return base_max
        if cfg.SNIPER_ADAPTIVE_MAX_RELAX <= 0:
            return base_max
        delta = cfg.SNIPER_COMPRESSION_RELAX_MAX_PCT - cfg.SNIPER_COMPRESSION_MAX_PCT
        ratio = min(1.0, self._adaptive_relax / cfg.SNIPER_ADAPTIVE_MAX_RELAX)
        return int(base_max + delta * ratio)

    def _regime_scores(self, snapshot: M15Snapshot, compression_max: int) -> tuple[str, float, float, float]:
        atr_val = snapshot.atr14
        if atr_val <= 0:
            return "transition", 0.0, 0.0, 0.0

        # Trend confidence
        pivot_score = 1.0 if snapshot.trend_state == "trend" else 0.3
        ema_sep = abs(snapshot.ema20 - snapshot.ema50) / atr_val
        ema_slope = abs(snapshot.ema20_slope) / atr_val if atr_val > 0 else 0.0
        ema_score = min(1.0, max(ema_sep, ema_slope) / 0.2)
        comp_trend_score = min(1.0, snapshot.compression_pct / 60.0)
        trend_conf = (pivot_score + ema_score + comp_trend_score) / 3.0

        # Range confidence
        width_score = 1.0 if snapshot.range_width >= cfg.RBH_RANGE_WIDTH_ATR * atr_val else 0.4
        touch_score = min(1.0, max(snapshot.touch_count_high, snapshot.touch_count_low) / 3.0)
        comp_range_score = 1.0 if snapshot.compression_pct <= compression_max else 0.4
        range_conf = (width_score + touch_score + comp_range_score) / 3.0

        if trend_conf >= range_conf:
            return "trend", max(trend_conf, range_conf), trend_conf, range_conf
        return "range", max(trend_conf, range_conf), trend_conf, range_conf

    def _fast_pass(self) -> list[FastCandidate]:
        candidates: list[FastCandidate] = []
        scannable = [
            sym for sym in self._universe
            if market_hours.is_good_session_for_symbol(sym)
        ]
        # per-symbol thresholds in loop

        for symbol in scannable:
            closed, forming_time = self._get_closed_m15(symbol, cfg.SNIPER_FAST_PASS_BARS + 2)
            if closed is None:
                continue
            snapshot = self._build_snapshot(symbol, closed, forming_time)
            if snapshot is None or snapshot.atr14 <= 0:
                continue
            profile = self._asset_profile(symbol)
            compression_max = self._effective_compression_max(profile.get("compression_max_pct", cfg.SNIPER_COMPRESSION_MAX_PCT))
            regime_min_conf = self._effective_regime_min_conf(profile.get("regime_min_conf", cfg.SNIPER_REGIME_MIN_CONF))

            # Regime detection with confidence
            base_regime, regime_conf, trend_conf, range_conf = self._regime_scores(snapshot, compression_max)
            if regime_conf < regime_min_conf:
                regime = "transition"
            else:
                regime = base_regime

            bias = "neutral"
            if regime == "trend":
                if snapshot.ema20 > snapshot.ema50 and snapshot.ema20_slope > 0:
                    bias = "long"
                elif snapshot.ema20 < snapshot.ema50 and snapshot.ema20_slope < 0:
                    bias = "short"

            spread_ok = snapshot.spread_atr_ratio <= profile.get("max_spread_atr", cfg.SNIPER_MAX_SPREAD_ATR)
            atr_ok = snapshot.atr14 > 0
            session_ok = True
            major_ok = True  # placeholder for macro-level proximity gating

            if not (spread_ok and atr_ok and session_ok and major_ok):
                continue

            spread_score = 1.0 if spread_ok else 0.4
            quick_score = 40.0
            quick_score += 25.0 if regime == "trend" else 20.0
            quick_score += 15.0 * spread_score
            quick_score += 10.0 if bias != "neutral" else 0.0

            candidates.append(FastCandidate(
                symbol=symbol,
                regime=regime,
                bias=bias,
                atr=snapshot.atr14,
                spread_atr_ratio=snapshot.spread_atr_ratio,
                quick_score=quick_score,
                regime_confidence=regime_conf,
                trend_conf=trend_conf,
                range_conf=range_conf,
                gates={
                    "session_ok": session_ok,
                    "spread_ok": spread_ok,
                    "atr_ok": atr_ok,
                    "major_level_ok": major_ok,
                },
            ))
        return candidates

    def on_bar_close(self) -> list[ExecutionIntent]:
        """Run fast pass + deep pass on new M15 bar close."""
        intents: list[ExecutionIntent] = []
        if not self._universe:
            self.refresh_universe()

        candidates = self._fast_pass()
        candidates.sort(key=lambda c: c.quick_score, reverse=True)
        shortlist = candidates[:cfg.SNIPER_SHORTLIST_MAX]

        intrabar_ranked: list[tuple[str, float]] = []
        current_bar_seq = 0

        for cand in shortlist:
            closed, forming_time = self._get_closed_m15(cand.symbol, cfg.SNIPER_CONTEXT_BARS + 2)
            if closed is None:
                continue
            snapshot = self._build_snapshot(cand.symbol, closed, forming_time)
            if snapshot is None:
                continue
            profile = self._asset_profile(cand.symbol)

            bar_time = int(closed.index[-1].timestamp())
            bar_seq = int(bar_time // _M15_SECONDS)
            if current_bar_seq == 0:
                current_bar_seq = bar_seq

            state = self._states.get(cand.symbol)
            if state is None:
                state = SymbolState(symbol=cand.symbol)
                self._states[cand.symbol] = state

            state.last_m15_bar_time = bar_seq
            state.last_fast_pass_time = bar_seq

            # Regime hysteresis
            if cand.regime == state.regime:
                state.regime_streak += 1
            else:
                state.regime = cand.regime
                state.regime_streak = 1
            state.regime_confidence = cand.regime_confidence

            regime_confirmed = (
                state.regime_streak >= cfg.SNIPER_REGIME_HYSTERESIS_BARS
                and cand.regime_confidence >= self._effective_regime_min_conf(
                    profile.get("regime_min_conf", cfg.SNIPER_REGIME_MIN_CONF)
                )
            )
            regime_use = state.regime if regime_confirmed else "transition"

            # Expire states
            if state.active_tpr and bar_seq > state.active_tpr.expires_at_bar:
                state.active_tpr = None
            if state.active_rbh and bar_seq > state.active_rbh.expires_at_bar:
                state.active_rbh = None
            if state.active_rbh and state.active_rbh.break_state in ("expired", "invalid"):
                state.active_rbh = None
            if state.active_ecr and bar_seq > state.active_ecr.expires_at_bar:
                state.active_ecr = None

            # Deep pass detection
            if regime_use == "trend":
                tpr_state = detect_tpr_setup(
                    cand.symbol,
                    closed,
                    snapshot.pivots,
                    snapshot.ema20,
                    snapshot.ema50,
                    snapshot.atr14,
                    snapshot.spread_atr_ratio,
                    bar_seq,
                    params=profile,
                )
                if tpr_state:
                    state.active_tpr = tpr_state
                    intrabar_ranked.append((cand.symbol, tpr_state.confidence))

                    # Bar-close trigger
                    closed_bar = closed.iloc[-1]
                    prev_bar = closed.iloc[-2]
                    trigger = check_tpr_trigger_on_close(
                        tpr_state,
                        closed_bar,
                        prev_bar,
                        snapshot.atr14,
                        snapshot.ema20,
                        params=profile,
                    )
                    if trigger:
                        is_rejection = "REJECTION" in trigger.reasons
                        use_pending = cfg.SNIPER_EXECUTION_STYLE in ("pending", "hybrid") and not is_rejection
                        entry_type = "market" if is_rejection else ("pending_stop" if use_pending else "market")
                        entry_price = trigger.trigger_price if is_rejection else tpr_state.trigger_level
                        intents.append(ExecutionIntent(
                            setup_type="TPR",
                            symbol=cand.symbol,
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
                        state.last_signal_time = bar_seq
                        state.active_tpr = None
                else:
                    state.active_tpr = None
            elif regime_use == "range":
                # Range regime -> RBH
                if state.active_rbh is None:
                    state.active_rbh = initialize_rbh_state(
                        cand.symbol,
                        closed,
                        snapshot.pivots,
                        snapshot.atr14,
                        snapshot.spread_atr_ratio,
                        bar_seq,
                        params=profile,
                    )

                if state.active_rbh:
                    updated, trigger = update_rbh_state(
                        state.active_rbh,
                        closed,
                        snapshot.atr14,
                        bar_seq,
                        params=profile,
                    )
                    state.active_rbh = updated
                    intrabar_ranked.append((cand.symbol, updated.confidence))
                    if trigger:
                        use_pending = cfg.SNIPER_EXECUTION_STYLE in ("pending", "hybrid")
                        intents.append(ExecutionIntent(
                            setup_type="RBH",
                            symbol=cand.symbol,
                            direction=trigger.direction,
                            entry_type="pending_limit" if use_pending else "market",
                            entry_price=updated.entry,
                            sl=updated.sl,
                            tp1=updated.tp1,
                            tp2=updated.tp2,
                            expiry_bar=cfg.SNIPER_PENDING_EXPIRY_BARS,
                            risk_factor=min(1.0, updated.confidence / 100),
                            atr=updated.atr,
                            trigger_level=updated.entry,
                            confidence=updated.confidence,
                            reasons=trigger.reasons,
                        ))
                        state.last_signal_time = bar_seq
                        state.active_rbh = None
            else:
                # Transition regime -> ECR
                if not profile.get("ecr_enabled", True):
                    continue
                if cand.trend_conf >= cfg.ECR_TREND_VETO_CONF:
                    continue
                if cfg.ECR_SESSION_ONLY and not market_hours.is_crypto_symbol(cand.symbol):
                    sessions = market_hours.active_sessions()
                    if not (set(sessions) & set(cfg.ECR_ALLOWED_SESSIONS)):
                        continue
                ecr_state, trigger = evaluate_ecr(
                    cand.symbol,
                    closed,
                    snapshot.atr14,
                    snapshot.spread_atr_ratio,
                    bar_seq,
                    snapshot.trend_state,
                    params=profile,
                )
                if ecr_state:
                    state.active_ecr = ecr_state
                if trigger and ecr_state:
                    use_pending = cfg.SNIPER_EXECUTION_STYLE in ("pending", "hybrid")
                    entry_type = "pending_limit" if use_pending else "market"
                    entry_price = ecr_state.trigger_level if use_pending else ecr_state.entry_price
                    intents.append(ExecutionIntent(
                        setup_type="ECR",
                        symbol=cand.symbol,
                        direction=trigger.direction,
                        entry_type=entry_type,
                        entry_price=entry_price,
                        sl=ecr_state.sl,
                        tp1=ecr_state.tp1,
                        tp2=ecr_state.tp2,
                        expiry_bar=2,
                        risk_factor=min(1.0, ecr_state.confidence / 100) * profile.get("ecr_risk_factor", cfg.ECR_RISK_FACTOR),
                        atr=ecr_state.atr,
                        trigger_level=ecr_state.trigger_level,
                        confidence=ecr_state.confidence,
                        reasons=trigger.reasons,
                    ))
                    state.last_signal_time = bar_seq
                    state.active_ecr = None

        # Intrabar shortlist
        intrabar_ranked.sort(key=lambda x: x[1], reverse=True)
        self._intrabar_symbols = [s for s, _ in intrabar_ranked[:cfg.SNIPER_INTRABAR_TOP_N]]

        log.info(
            f"[SNIPER] bar close intents={len(intents)} shortlist={len(shortlist)} "
            f"intrabar={len(self._intrabar_symbols)}"
        )
        # Adaptive gating updates after this bar
        if intents:
            self._adaptive_relax = 0.0
            self._last_signal_bar = current_bar_seq or self._last_signal_bar
        elif cfg.SNIPER_ADAPTIVE_ENABLED and current_bar_seq and self._last_signal_bar:
            idle_bars = max(0, current_bar_seq - self._last_signal_bar)
            if idle_bars >= cfg.SNIPER_ADAPTIVE_IDLE_BARS:
                self._adaptive_relax = min(
                    cfg.SNIPER_ADAPTIVE_MAX_RELAX,
                    self._adaptive_relax + cfg.SNIPER_ADAPTIVE_RELAX_STEP,
                )
        return intents

    def intrabar_check(self) -> list[ExecutionIntent]:
        intents: list[ExecutionIntent] = []
        if cfg.SNIPER_EXECUTION_STYLE == "market_close":
            return intents
        if not self._intrabar_symbols:
            return intents

        for symbol in self._intrabar_symbols:
            state = self._states.get(symbol)
            if not state or not state.active_tpr:
                continue
            tpr_state = state.active_tpr
            if state.last_m15_bar_time > tpr_state.expires_at_bar:
                continue
            if state.last_signal_time == state.last_m15_bar_time:
                continue
            tick = self.mt5.symbol_tick(symbol)
            if not tick:
                continue
            price = tick["ask"] if tpr_state.direction == "BUY" else tick["bid"]
            trigger = check_tpr_trigger_intrabar(tpr_state, price, tpr_state.atr)
            if trigger:
                intents.append(ExecutionIntent(
                    setup_type="TPR",
                    symbol=symbol,
                    direction=trigger.direction,
                    entry_type="market",
                    entry_price=price,
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
                state.last_signal_time = state.last_m15_bar_time
                state.active_tpr = None
        return intents
