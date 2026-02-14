"""
Trend Pullback Reclaim (TPR) setup detection and triggers.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

import config as cfg
from .levels import last_swings
from .scoring import score_tpr
from .state import TPRSetupState, TriggerEvent


def _tp_levels(entry: float, sl: float, direction: str, swing_target: float | None) -> tuple[float, float]:
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0, 0.0
    r1 = risk * cfg.SNIPER_TP_R1
    r2 = risk * cfg.SNIPER_TP_R2
    if direction == "BUY":
        tp1 = swing_target if swing_target and swing_target > entry else entry + r1
        tp2 = entry + r2
    else:
        tp1 = swing_target if swing_target and swing_target < entry else entry - r1
        tp2 = entry - r2
    return tp1, tp2


def detect_tpr_setup(
    symbol: str,
    df: pd.DataFrame,
    pivots,
    ema20: float,
    ema50: float,
    atr_val: float,
    spread_atr_ratio: float,
    bar_index: int,
    params: dict | None = None,
) -> Optional[TPRSetupState]:
    params = params or {}
    pullback_atr = params.get("tpr_pullback_atr", cfg.TPR_PULLBACK_ATR)
    invalidation_atr = params.get("tpr_invalidation_atr", cfg.TPR_INVALIDATION_ATR)
    sl_buffer_atr = params.get("tpr_sl_buffer_atr", cfg.TPR_SL_BUFFER_ATR)
    min_stop_atr = params.get("min_stop_atr", cfg.SNIPER_MIN_STOP_ATR)
    no_chase_atr = params.get("no_chase_atr", cfg.SNIPER_NO_CHASE_ATR)
    max_spread_atr = params.get("max_spread_atr", cfg.SNIPER_MAX_SPREAD_ATR)

    if df is None or len(df) < 30 or atr_val <= 0:
        return None

    swings = last_swings(pivots)
    highs = swings["highs"]
    lows = swings["lows"]
    if len(highs) < 2 or len(lows) < 2:
        return None

    last_close = float(df["close"].iloc[-1])

    # Determine trend direction from pivots
    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price

    if hh and hl:
        direction = "BUY"
    elif lh and ll:
        direction = "SELL"
    else:
        return None

    # Pullback zone check
    ema_band_lo = min(ema20, ema50)
    ema_band_hi = max(ema20, ema50)

    if direction == "BUY":
        s_low_recent = lows[-1].price
        s_low_prev = lows[-2].price
        s_high_recent = highs[-1].price
        in_zone = (
            ema_band_lo <= last_close <= ema_band_hi
            or abs(last_close - s_low_recent) <= pullback_atr * atr_val
        )
        if s_low_recent <= s_low_prev or not in_zone:
            return None

        pb_start_idx = highs[-1].idx
        pullback_df = df.iloc[pb_start_idx:]
        if pullback_df.empty:
            return None
        pullback_low = float(pullback_df["low"].min())
        pullback_high = float(pullback_df["high"].max())
        pb_high = pullback_high

        # Invalidation: close below HL - buffer
        if (pullback_df["close"] < s_low_recent - invalidation_atr * atr_val).any():
            return None

        sl = min(s_low_recent, pullback_low) - sl_buffer_atr * atr_val
        if abs(pb_high - sl) < min_stop_atr * atr_val:
            return None
        tp1, tp2 = _tp_levels(pb_high, sl, direction, s_high_recent)
        no_chase_max = pb_high + no_chase_atr * atr_val

    else:
        s_high_recent = highs[-1].price
        s_high_prev = highs[-2].price
        s_low_recent = lows[-1].price
        in_zone = (
            ema_band_lo <= last_close <= ema_band_hi
            or abs(last_close - s_high_recent) <= pullback_atr * atr_val
        )
        if s_high_recent >= s_high_prev or not in_zone:
            return None

        pb_start_idx = lows[-1].idx
        pullback_df = df.iloc[pb_start_idx:]
        if pullback_df.empty:
            return None
        pullback_low = float(pullback_df["low"].min())
        pullback_high = float(pullback_df["high"].max())
        pb_low = pullback_low

        if (pullback_df["close"] > s_high_recent + invalidation_atr * atr_val).any():
            return None

        sl = max(s_high_recent, pullback_high) + sl_buffer_atr * atr_val
        if abs(pb_low - sl) < min_stop_atr * atr_val:
            return None
        tp1, tp2 = _tp_levels(pb_low, sl, direction, s_low_recent)
        no_chase_max = pb_low - no_chase_atr * atr_val

    # Scoring components
    structure = 1.0
    ema_align = 1.0 if ((ema20 > ema50 and direction == "BUY") or (ema20 < ema50 and direction == "SELL")) else 0.5
    ema_slope = 1.0 if abs(ema20 - ema50) > 0 else 0.5
    pullback_depth = 0.7
    spread_score = 1.0 if spread_atr_ratio <= max_spread_atr else 0.4
    momentum = 0.5

    score, breakdown = score_tpr({
        "structure": structure,
        "ema": min(1.0, (ema_align + ema_slope) / 2),
        "pullback": pullback_depth,
        "spread": spread_score,
        "momentum": momentum,
    })

    return TPRSetupState(
        symbol=symbol,
        direction=direction,
        detected_at_time=int(df.index[-1].timestamp()),
        expires_at_bar=bar_index + cfg.TPR_EXPIRY_BARS,
        atr=atr_val,
        swing_low_recent=lows[-1].price if direction == "BUY" else s_low_recent,
        swing_low_prev=lows[-2].price if direction == "BUY" else 0.0,
        swing_high_recent=highs[-1].price if direction == "BUY" else s_high_recent,
        pullback_start_time=int(df.index[pb_start_idx].timestamp()),
        pullback_low=float(pullback_df["low"].min()),
        pullback_high=float(pullback_df["high"].max()),
        pb_high=pb_high if direction == "BUY" else pb_low,
        in_pullback_zone=True,
        structure_intact=True,
        invalidated=False,
        trigger_level=pb_high if direction == "BUY" else pb_low,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        no_chase_max=no_chase_max,
        confidence=score,
        score_breakdown=breakdown,
    )


def check_tpr_trigger_on_close(
    state: TPRSetupState,
    closed_bar: pd.Series,
    prev_bar: pd.Series,
    atr_val: float,
    ema20: float,
    params: dict | None = None,
) -> Optional[TriggerEvent]:
    params = params or {}
    trigger_body_atr = params.get("tpr_trigger_body_atr", cfg.TPR_TRIGGER_BODY_ATR)
    rejection_enabled = params.get("tpr_rejection_enabled", cfg.TPR_REJECTION_ENABLED)
    if atr_val <= 0:
        return None

    c_open = float(closed_bar["open"])
    c_close = float(closed_bar["close"])
    c_high = float(closed_bar["high"])
    c_low = float(closed_bar["low"])
    c_body = abs(c_close - c_open)

    if state.direction == "BUY":
        if c_high >= state.trigger_level and c_body >= trigger_body_atr * atr_val:
            return TriggerEvent(
                setup_type="TPR",
                symbol=state.symbol,
                direction="BUY",
                trigger_time=int(closed_bar.name.timestamp()),
                trigger_price=state.trigger_level,
                momentum_score=min(1.0, c_body / (trigger_body_atr * atr_val)),
                reasons=["PBHIGH_RECLAIM", "BODY_MOMENTUM"],
            )
        # Rejection candle option
        if rejection_enabled:
            lower_wick = min(c_open, c_close) - c_low
            if lower_wick >= c_body * 1.2 and c_close > ema20:
                return TriggerEvent(
                    setup_type="TPR",
                    symbol=state.symbol,
                    direction="BUY",
                    trigger_time=int(closed_bar.name.timestamp()),
                    trigger_price=c_close,
                    momentum_score=0.6,
                    reasons=["REJECTION", "CLOSE_ABOVE_EMA20"],
                )
    else:
        if c_low <= state.trigger_level and c_body >= trigger_body_atr * atr_val:
            return TriggerEvent(
                setup_type="TPR",
                symbol=state.symbol,
                direction="SELL",
                trigger_time=int(closed_bar.name.timestamp()),
                trigger_price=state.trigger_level,
                momentum_score=min(1.0, c_body / (trigger_body_atr * atr_val)),
                reasons=["PBLOW_RECLAIM", "BODY_MOMENTUM"],
            )
        if rejection_enabled:
            upper_wick = c_high - max(c_open, c_close)
            if upper_wick >= c_body * 1.2 and c_close < ema20:
                return TriggerEvent(
                    setup_type="TPR",
                    symbol=state.symbol,
                    direction="SELL",
                    trigger_time=int(closed_bar.name.timestamp()),
                    trigger_price=c_close,
                    momentum_score=0.6,
                    reasons=["REJECTION", "CLOSE_BELOW_EMA20"],
                )
    return None


def check_tpr_trigger_intrabar(
    state: TPRSetupState,
    price: float,
    atr_val: float,
) -> Optional[TriggerEvent]:
    if atr_val <= 0:
        return None
    if state.direction == "BUY":
        if price >= state.trigger_level and price <= state.no_chase_max:
            return TriggerEvent(
                setup_type="TPR",
                symbol=state.symbol,
                direction="BUY",
                trigger_time=0,
                trigger_price=state.trigger_level,
                momentum_score=0.5,
                reasons=["INTRABAR_RECLAIM"],
            )
    else:
        if price <= state.trigger_level and price >= state.no_chase_max:
            return TriggerEvent(
                setup_type="TPR",
                symbol=state.symbol,
                direction="SELL",
                trigger_time=0,
                trigger_price=state.trigger_level,
                momentum_score=0.5,
                reasons=["INTRABAR_RECLAIM"],
            )
    return None
