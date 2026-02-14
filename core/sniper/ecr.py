"""
EMA Cycle Reversion (ECR) setup detection.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from .levels import ema
from .scoring import score_ecr
from .state import ECRSetupState, TriggerEvent


def _cross_count(series_a: pd.Series, series_b: pd.Series, min_gap: int) -> int:
    diff = series_a - series_b
    sign = np.sign(diff)
    crosses = np.where(np.diff(sign) != 0)[0]
    if len(crosses) == 0:
        return 0
    count = 1
    last_idx = crosses[0]
    for idx in crosses[1:]:
        if idx - last_idx >= min_gap:
            count += 1
            last_idx = idx
    return count


def _last_cross_index(series_a: pd.Series, series_b: pd.Series) -> int:
    diff = series_a - series_b
    sign = np.sign(diff)
    crosses = np.where(np.diff(sign) != 0)[0]
    if len(crosses) == 0:
        return -1
    return int(crosses[-1])


def evaluate_ecr(
    symbol: str,
    df: pd.DataFrame,
    atr_val: float,
    spread_atr_ratio: float,
    bar_index: int,
    trend_state: str,
    params: dict | None = None,
) -> tuple[Optional[ECRSetupState], Optional[TriggerEvent]]:
    params = params or {}
    max_ema50_slope_atr = params.get("ecr_max_ema50_slope_atr", cfg.ECR_MAX_EMA50_SLOPE_ATR)
    max_target_atr = params.get("ecr_max_target_atr", cfg.ECR_MAX_TARGET_ATR)
    entry_body_atr = params.get("ecr_entry_body_atr", cfg.ECR_ENTRY_BODY_ATR)
    max_spread_atr = params.get("ecr_max_spread_atr", cfg.ECR_MAX_SPREAD_ATR)
    min_score = params.get("ecr_min_score", cfg.ECR_MIN_SCORE)
    no_chase_atr = params.get("no_chase_atr", cfg.SNIPER_NO_CHASE_ATR)

    if df is None or len(df) < max(220, cfg.ECR_CROSS_WINDOW_BARS + 10):
        return None, None
    if atr_val <= 0:
        return None, None

    close = df["close"]
    ema5 = ema(close, cfg.ECR_FAST_EMA)
    ema13 = ema(close, cfg.ECR_SIGNAL_EMA)
    ema50 = ema(close, cfg.ECR_TREND_EMA)
    ema200 = ema(close, cfg.ECR_TARGET_EMA)

    last_close = float(close.iloc[-1])
    ema13_val = float(ema13.iloc[-1])
    ema50_val = float(ema50.iloc[-1])
    ema200_val = float(ema200.iloc[-1])

    # Trend strength gate (avoid strong trends)
    ema50_slope = (ema50.iloc[-1] - ema50.iloc[-5]) / atr_val if len(ema50) > 6 else 0
    if abs(float(ema50_slope)) > max_ema50_slope_atr:
        return None, None

    # Use only in transition/range regimes
    if trend_state not in ("transition", "range"):
        return None, None

    # Determine counter-trend direction from 13/50 relationship
    if ema13_val < ema50_val:
        direction = "BUY"
    elif ema13_val > ema50_val:
        direction = "SELL"
    else:
        return None, None

    # Require last 13/50 cross within window
    last_cross = _last_cross_index(ema13.iloc[-cfg.ECR_CROSS_WINDOW_BARS:], ema50.iloc[-cfg.ECR_CROSS_WINDOW_BARS:])
    if last_cross < 0:
        return None, None
    trend_cross_time = int(df.index[-cfg.ECR_CROSS_WINDOW_BARS + last_cross].timestamp())

    # Count 5/13 cycles since trend cross
    ema5_window = ema5.iloc[-cfg.ECR_CROSS_WINDOW_BARS:]
    ema13_window = ema13.iloc[-cfg.ECR_CROSS_WINDOW_BARS:]
    cycle_crosses = _cross_count(ema5_window, ema13_window, cfg.ECR_CROSS_MIN_GAP_BARS)
    if cycle_crosses < cfg.ECR_CROSS_COUNT:
        return None, None

    # EMA200 distance gate
    dist_to_ema200 = abs(last_close - ema200_val)
    if dist_to_ema200 > max_target_atr * atr_val:
        return None, None
    if direction == "BUY" and ema200_val <= last_close:
        return None, None
    if direction == "SELL" and ema200_val >= last_close:
        return None, None

    # Spread gate (stricter for ECR)
    if spread_atr_ratio > max_spread_atr:
        return None, None

    # Entry condition: candle close beyond EMA13 with momentum
    closed_bar = df.iloc[-1]
    c_open = float(closed_bar["open"])
    c_close = float(closed_bar["close"])
    c_body = abs(c_close - c_open)
    if c_body < entry_body_atr * atr_val:
        return None, None

    if direction == "BUY" and (c_close <= ema13_val or c_close <= float(ema5.iloc[-1])):
        return None, None
    if direction == "SELL" and (c_close >= ema13_val or c_close >= float(ema5.iloc[-1])):
        return None, None

    # Require last 5/13 cross to align with entry direction
    last_cross = _last_cross_index(ema5_window, ema13_window)
    if last_cross >= 0:
        idx = -cfg.ECR_CROSS_WINDOW_BARS + last_cross
        ema5_last = float(ema5.iloc[idx])
        ema13_last = float(ema13.iloc[idx])
        if direction == "BUY" and ema5_last < ema13_last:
            return None, None
        if direction == "SELL" and ema5_last > ema13_last:
            return None, None

    # Stop placement
    if direction == "BUY":
        swing_low = float(df["low"].iloc[-cfg.ECR_STOP_LOOKBACK:].min())
        sl = swing_low - cfg.ECR_SL_BUFFER_ATR * atr_val
    else:
        swing_high = float(df["high"].iloc[-cfg.ECR_STOP_LOOKBACK:].max())
        sl = swing_high + cfg.ECR_SL_BUFFER_ATR * atr_val

    if abs(last_close - sl) < cfg.SNIPER_MIN_STOP_ATR * atr_val:
        return None, None

    # Targets: TP1 at EMA200, TP2 at R-multiple capped by EMA200
    risk = abs(last_close - sl)
    tp2 = last_close + cfg.SNIPER_TP_R2 * risk if direction == "BUY" else last_close - cfg.SNIPER_TP_R2 * risk
    tp1 = ema200_val
    if direction == "BUY":
        tp2 = min(tp2, ema200_val)
    else:
        tp2 = max(tp2, ema200_val)

    components = {
        "cycle": min(1.0, cycle_crosses / max(1, cfg.ECR_CROSS_COUNT)),
        "trend": 0.7,
        "ema200": max(0.3, 1.0 - (dist_to_ema200 / (max_target_atr * atr_val))),
        "spread": 1.0 if spread_atr_ratio <= max_spread_atr else 0.4,
        "momentum": min(1.0, c_body / (entry_body_atr * atr_val)),
    }
    score, breakdown = score_ecr(components)
    if score < min_score:
        return None, None

    state = ECRSetupState(
        symbol=symbol,
        direction=direction,
        detected_at_time=int(df.index[-1].timestamp()),
        expires_at_bar=bar_index + 2,
        trend_cross_time=trend_cross_time,
        cycle_cross_count=cycle_crosses,
        entry_price=last_close,
        trigger_level=ema13_val,
        atr=atr_val,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        no_chase_max=last_close + no_chase_atr * atr_val if direction == "BUY"
        else last_close - no_chase_atr * atr_val,
        confidence=score,
        score_breakdown=breakdown,
    )

    trigger = TriggerEvent(
        setup_type="ECR",
        symbol=symbol,
        direction=direction,
        trigger_time=int(df.index[-1].timestamp()),
        trigger_price=last_close,
        momentum_score=components["momentum"],
        reasons=[
            f"CYCLES={cycle_crosses}",
            "CLOSE_THROUGH_EMA13",
            "TARGET_EMA200",
        ],
    )
    return state, trigger
