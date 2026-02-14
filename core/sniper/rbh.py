"""
Range Breakout Retest Hold (RBH) setup detection and triggers.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

import config as cfg
from .levels import detect_range, atr_percentile
from .scoring import score_rbh
from .state import RBHSetupState, TriggerEvent


def initialize_rbh_state(
    symbol: str,
    df: pd.DataFrame,
    pivots,
    atr_val: float,
    spread_atr_ratio: float,
    bar_index: int,
    params: dict | None = None,
) -> Optional[RBHSetupState]:
    params = params or {}
    range_width_atr = params.get("rbh_range_width_atr", cfg.RBH_RANGE_WIDTH_ATR)
    touch_tol_atr = params.get("rbh_touch_tol_atr", cfg.RBH_TOUCH_TOL_ATR)
    break_body_atr = params.get("rbh_break_body_atr", cfg.RBH_BREAK_BODY_ATR)
    retest_tol_atr = params.get("rbh_retest_tol_atr", cfg.RBH_RETEST_TOL_ATR)
    sl_buffer_atr = params.get("rbh_sl_buffer_atr", cfg.RBH_SL_BUFFER_ATR)
    max_spread_atr = params.get("max_spread_atr", cfg.SNIPER_MAX_SPREAD_ATR)

    if atr_val <= 0 or df is None or len(df) < 60:
        return None

    range_info = detect_range(
        pivots,
        atr_val,
        cfg.SNIPER_RANGE_LOOKBACK_BARS,
        touch_tol_atr,
    )
    if range_info.width <= 0:
        return None

    if range_info.width < range_width_atr * atr_val:
        return None

    atr_series = df.get("atr")
    compression_pct = 100.0
    if atr_series is not None:
        compression_pct = atr_percentile(atr_series, cfg.SNIPER_COMPRESSION_BARS)
    compression_ok = compression_pct <= 40.0

    last_close = float(df["close"].iloc[-1])
    near_high = abs(last_close - range_info.range_high) <= atr_val * 0.3
    near_low = abs(last_close - range_info.range_low) <= atr_val * 0.3

    if near_high:
        direction = "BUY"
    elif near_low:
        direction = "SELL"
    else:
        return None

    score, breakdown = score_rbh({
        "range": min(1.0, max(range_info.touch_high, range_info.touch_low) / 3),
        "compression": 1.0 if compression_ok else 0.4,
        "break": 0.4,
        "retest": 0.4,
        "spread": 1.0 if spread_atr_ratio <= max_spread_atr else 0.4,
    })

    return RBHSetupState(
        symbol=symbol,
        direction=direction,
        detected_at_time=int(df.index[-1].timestamp()),
        expires_at_bar=bar_index + cfg.RBH_RETEST_WINDOW_BARS,
        atr=atr_val,
        range_high=range_info.range_high,
        range_low=range_info.range_low,
        range_width=range_info.width,
        touch_count_high=range_info.touch_high,
        touch_count_low=range_info.touch_low,
        compression_ok=compression_ok,
        confidence=score,
        score_breakdown=breakdown,
    )


def update_rbh_state(
    state: RBHSetupState,
    df: pd.DataFrame,
    atr_val: float,
    bar_index: int,
    params: dict | None = None,
) -> tuple[RBHSetupState, Optional[TriggerEvent]]:
    params = params or {}
    break_buffer_atr = params.get("rbh_break_buffer_atr", cfg.RBH_BREAK_BUFFER_ATR)
    break_body_atr = params.get("rbh_break_body_atr", cfg.RBH_BREAK_BODY_ATR)
    retest_tol_atr = params.get("rbh_retest_tol_atr", cfg.RBH_RETEST_TOL_ATR)
    sl_buffer_atr = params.get("rbh_sl_buffer_atr", cfg.RBH_SL_BUFFER_ATR)
    min_stop_atr = params.get("min_stop_atr", cfg.SNIPER_MIN_STOP_ATR)

    if atr_val <= 0 or df is None or len(df) < 30:
        return state, None

    closed_bar = df.iloc[-1]
    c_open = float(closed_bar["open"])
    c_close = float(closed_bar["close"])
    c_high = float(closed_bar["high"])
    c_low = float(closed_bar["low"])
    c_body = abs(c_close - c_open)
    c_range = c_high - c_low if c_high > c_low else 0.0

    # If break not confirmed yet
    if state.break_time == 0:
        if state.direction == "BUY":
            if (c_close >= state.range_high + break_buffer_atr * atr_val
                    and c_body >= break_body_atr * atr_val
                    and c_close >= c_low + 0.7 * (c_range or 1)):
                state.break_time = int(closed_bar.name.timestamp())
                state.break_level = state.range_high
                state.break_candle_high = c_high
                state.retest_window_end = bar_index + cfg.RBH_RETEST_WINDOW_BARS
        else:
            if (c_close <= state.range_low - break_buffer_atr * atr_val
                    and c_body >= break_body_atr * atr_val
                    and c_close <= c_high - 0.7 * (c_range or 1)):
                state.break_time = int(closed_bar.name.timestamp())
                state.break_level = state.range_low
                state.break_candle_high = c_low
                state.retest_window_end = bar_index + cfg.RBH_RETEST_WINDOW_BARS

        return state, None

    # Expire if no retest in window
    if bar_index > state.retest_window_end:
        state.break_state = "expired"
        return state, None

    # Retest hold confirmation
    if state.direction == "BUY":
        if c_close < state.range_high - break_buffer_atr * atr_val:
            state.break_state = "invalid"
            return state, None
        touched = c_low <= state.break_level + retest_tol_atr * atr_val
        holds = c_close >= state.break_level
        if touched and holds:
            state.retest_confirmed = True
            entry = state.break_level + 0.05 * atr_val
            retest_low = float(df["low"].iloc[-3:].min())
            sl = min(retest_low, state.break_level - break_buffer_atr * atr_val) - sl_buffer_atr * atr_val
            if abs(entry - sl) < min_stop_atr * atr_val:
                state.break_state = "invalid"
                return state, None
            tp1 = entry + state.range_width * 0.6
            tp2 = entry + state.range_width
            state.entry = entry
            state.sl = sl
            state.tp1 = tp1
            state.tp2 = tp2
            return state, TriggerEvent(
                setup_type="RBH",
                symbol=state.symbol,
                direction="BUY",
                trigger_time=int(closed_bar.name.timestamp()),
                trigger_price=entry,
                momentum_score=min(1.0, c_body / (break_body_atr * atr_val)),
                reasons=["RETEST_HOLD", "CLOSE_ABOVE_RANGE"],
            )
    else:
        if c_close > state.range_low + break_buffer_atr * atr_val:
            state.break_state = "invalid"
            return state, None
        touched = c_high >= state.break_level - retest_tol_atr * atr_val
        holds = c_close <= state.break_level
        if touched and holds:
            state.retest_confirmed = True
            entry = state.break_level - 0.05 * atr_val
            retest_high = float(df["high"].iloc[-3:].max())
            sl = max(retest_high, state.break_level + break_buffer_atr * atr_val) + sl_buffer_atr * atr_val
            if abs(entry - sl) < min_stop_atr * atr_val:
                state.break_state = "invalid"
                return state, None
            tp1 = entry - state.range_width * 0.6
            tp2 = entry - state.range_width
            state.entry = entry
            state.sl = sl
            state.tp1 = tp1
            state.tp2 = tp2
            return state, TriggerEvent(
                setup_type="RBH",
                symbol=state.symbol,
                direction="SELL",
                trigger_time=int(closed_bar.name.timestamp()),
                trigger_price=entry,
                momentum_score=min(1.0, c_body / (break_body_atr * atr_val)),
                reasons=["RETEST_HOLD", "CLOSE_BELOW_RANGE"],
            )

    return state, None
