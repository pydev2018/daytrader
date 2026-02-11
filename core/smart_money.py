"""
===============================================================================
  Smart Money Concepts — Institutional Order Flow Detection
===============================================================================
  Detects patterns that reveal how "smart money" (institutions) move:
    • Order Blocks (OB)
    • Fair Value Gaps (FVG)
    • Liquidity Sweeps / Stop Hunts
    • Break of Structure (BOS)
    • Change of Character (CHoCH)
===============================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as cfg
from core.patterns import find_swing_points
from utils.logger import get_logger

log = get_logger("smart_money")


# ═════════════════════════════════════════════════════════════════════════════
#  ORDER BLOCKS
# ═════════════════════════════════════════════════════════════════════════════

def find_order_blocks(df: pd.DataFrame, lookback: int | None = None) -> list[dict]:
    """
    An Order Block is the last opposing candle before a strong impulsive move.

    Bullish OB: last bearish candle before a strong bullish move.
    Bearish OB: last bullish candle before a strong bearish move.

    These zones act as institutional entry areas where smart money placed
    large orders, creating an imbalance.
    """
    lookback = lookback or cfg.ORDER_BLOCK_LOOKBACK
    if df is None or len(df) < 10 or "atr" not in df.columns:
        return []

    blocks = []
    closes = df["close"].values
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    atrs = df["atr"].values

    start = max(3, len(df) - lookback)

    for i in range(start, len(df) - 1):
        atr_val = atrs[i]
        if np.isnan(atr_val) or atr_val == 0:
            continue

        # Check if bar i+1 is an impulsive move (> 1.5x ATR body)
        next_body = abs(closes[i + 1] - opens[i + 1])
        if next_body < 1.5 * atr_val:
            continue

        is_bullish_impulse = closes[i + 1] > opens[i + 1]
        is_bearish_candle = closes[i] < opens[i]
        is_bullish_candle = closes[i] > opens[i]

        # Bullish OB: bearish candle → bullish impulse
        if is_bearish_candle and is_bullish_impulse:
            blocks.append({
                "type": "bullish_ob",
                "bias": 1,
                "ob_high": highs[i],
                "ob_low": lows[i],
                "time": df.index[i],
                "strength": round(next_body / atr_val, 2),
                "mitigated": False,
            })

        # Bearish OB: bullish candle → bearish impulse
        if is_bullish_candle and not is_bullish_impulse:
            blocks.append({
                "type": "bearish_ob",
                "bias": -1,
                "ob_high": highs[i],
                "ob_low": lows[i],
                "time": df.index[i],
                "strength": round(next_body / atr_val, 2),
                "mitigated": False,
            })

    # Mark mitigated OBs (price has already returned to the OB)
    current_price = closes[-1]
    for ob in blocks:
        if ob["bias"] == 1 and current_price < ob["ob_low"]:
            ob["mitigated"] = True
        if ob["bias"] == -1 and current_price > ob["ob_high"]:
            ob["mitigated"] = True

    # Filter to unmitigated only (fresh)
    fresh = [ob for ob in blocks if not ob["mitigated"]]
    return fresh


# ═════════════════════════════════════════════════════════════════════════════
#  FAIR VALUE GAPS (FVG) — Imbalances
# ═════════════════════════════════════════════════════════════════════════════

def find_fair_value_gaps(df: pd.DataFrame) -> list[dict]:
    """
    A Fair Value Gap is a three-candle pattern where there is a gap
    between candle 1's wick and candle 3's wick, with candle 2 being impulsive.

    Bullish FVG: candle_1.high < candle_3.low (gap up)
    Bearish FVG: candle_1.low > candle_3.high (gap down)

    These gaps tend to get filled — price is "attracted" to them.
    """
    if df is None or len(df) < 10 or "atr" not in df.columns:
        return []

    gaps = []
    highs = df["high"].values
    lows = df["low"].values
    atrs = df["atr"].values

    lookback_start = max(2, len(df) - cfg.ORDER_BLOCK_LOOKBACK)

    for i in range(lookback_start, len(df) - 2):
        atr_val = atrs[i + 1]
        if np.isnan(atr_val) or atr_val == 0:
            continue

        c1_high = highs[i]
        c3_low = lows[i + 2]

        # Bullish FVG: gap between candle 1 high and candle 3 low
        if c3_low > c1_high:
            gap_size = c3_low - c1_high
            if gap_size >= cfg.FVG_MIN_GAP_ATR_MULT * atr_val:
                gaps.append({
                    "type": "bullish_fvg",
                    "bias": 1,
                    "gap_high": c3_low,
                    "gap_low": c1_high,
                    "time": df.index[i + 1],
                    "size_atr": round(gap_size / atr_val, 2),
                    "filled": False,
                })

        c1_low = lows[i]
        c3_high = highs[i + 2]

        # Bearish FVG: gap between candle 1 low and candle 3 high
        if c1_low > c3_high:
            gap_size = c1_low - c3_high
            if gap_size >= cfg.FVG_MIN_GAP_ATR_MULT * atr_val:
                gaps.append({
                    "type": "bearish_fvg",
                    "bias": -1,
                    "gap_high": c1_low,
                    "gap_low": c3_high,
                    "time": df.index[i + 1],
                    "size_atr": round(gap_size / atr_val, 2),
                    "filled": False,
                })

    # Check if gaps have been filled
    current_price = df["close"].values[-1]
    for gap in gaps:
        if gap["bias"] == 1:
            # Bullish FVG filled if price has come down into the gap
            if current_price <= gap["gap_high"]:
                gap["filled"] = True
        else:
            # Bearish FVG filled if price has risen into the gap
            if current_price >= gap["gap_low"]:
                gap["filled"] = True

    return [g for g in gaps if not g["filled"]]


# ═════════════════════════════════════════════════════════════════════════════
#  LIQUIDITY SWEEPS / STOP HUNTS
# ═════════════════════════════════════════════════════════════════════════════

def find_liquidity_sweeps(df: pd.DataFrame, lookback: int | None = None) -> list[dict]:
    """
    A liquidity sweep occurs when price briefly pokes past a swing high/low
    (sweeping stop losses) and then reverses sharply.

    This is the "trapped traders" concept from the book — traders enter on
    what looks like a breakout, but get trapped as price reverses.
    """
    lookback = lookback or cfg.LIQUIDITY_SWEEP_LOOKBACK
    if df is None or len(df) < lookback + 5:
        return []

    swing_highs, swing_lows = find_swing_points(df.iloc[:-3], lookback=cfg.SWING_LOOKBACK)
    sweeps = []

    last_3 = df.iloc[-3:]
    current = df.iloc[-1]

    # Check if recent candles swept a swing high then reversed
    for t, sh in swing_highs[-5:]:
        for idx in range(len(last_3)):
            bar = last_3.iloc[idx]
            if bar["high"] > sh and bar["close"] < sh:
                # Wick above swing high but closed below → bearish sweep
                sweeps.append({
                    "type": "bearish_sweep",
                    "bias": -1,
                    "level_swept": sh,
                    "sweep_high": bar["high"],
                    "time": last_3.index[idx],
                    "strength": 0.8,
                })
                break

    # Check if recent candles swept a swing low then reversed
    for t, sl in swing_lows[-5:]:
        for idx in range(len(last_3)):
            bar = last_3.iloc[idx]
            if bar["low"] < sl and bar["close"] > sl:
                # Wick below swing low but closed above → bullish sweep
                sweeps.append({
                    "type": "bullish_sweep",
                    "bias": 1,
                    "level_swept": sl,
                    "sweep_low": bar["low"],
                    "time": last_3.index[idx],
                    "strength": 0.8,
                })
                break

    return sweeps


# ═════════════════════════════════════════════════════════════════════════════
#  BREAK OF STRUCTURE (BOS) & CHANGE OF CHARACTER (CHoCH)
# ═════════════════════════════════════════════════════════════════════════════

def detect_structure_breaks(df: pd.DataFrame) -> list[dict]:
    """
    BOS: Price breaks a swing high/low in the direction of the trend
         (confirms trend continuation).
    CHoCH: Price breaks a swing high/low AGAINST the trend
           (early warning of reversal).
    """
    if df is None or len(df) < 30:
        return []

    swing_highs, swing_lows = find_swing_points(df, cfg.SWING_LOOKBACK)

    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return []

    events = []
    current_close = df["close"].values[-1]

    # Determine prevailing structure
    recent_highs = [p for _, p in swing_highs[-3:]]
    recent_lows = [p for _, p in swing_lows[-3:]]

    # In uptrend: HH & HL
    was_uptrend = (
        len(recent_highs) >= 2
        and recent_highs[-1] > recent_highs[-2]
        and len(recent_lows) >= 2
        and recent_lows[-1] > recent_lows[-2]
    )

    # In downtrend: LH & LL
    was_downtrend = (
        len(recent_highs) >= 2
        and recent_highs[-1] < recent_highs[-2]
        and len(recent_lows) >= 2
        and recent_lows[-1] < recent_lows[-2]
    )

    last_swing_high = swing_highs[-1][1] if swing_highs else None
    last_swing_low = swing_lows[-1][1] if swing_lows else None
    prev_swing_low = swing_lows[-2][1] if len(swing_lows) >= 2 else None
    prev_swing_high = swing_highs[-2][1] if len(swing_highs) >= 2 else None

    # BOS bullish: in uptrend, price breaks above last swing high
    if was_uptrend and last_swing_high and current_close > last_swing_high:
        events.append({
            "type": "bos_bullish",
            "bias": 1,
            "level": last_swing_high,
            "strength": 0.7,
        })

    # BOS bearish: in downtrend, price breaks below last swing low
    if was_downtrend and last_swing_low and current_close < last_swing_low:
        events.append({
            "type": "bos_bearish",
            "bias": -1,
            "level": last_swing_low,
            "strength": 0.7,
        })

    # CHoCH bullish: in downtrend, price breaks above last swing HIGH (reversal!)
    if was_downtrend and last_swing_high and current_close > last_swing_high:
        events.append({
            "type": "choch_bullish",
            "bias": 1,
            "level": last_swing_high,
            "strength": 0.85,
        })

    # CHoCH bearish: in uptrend, price breaks below last swing LOW (reversal!)
    if was_uptrend and last_swing_low and current_close < last_swing_low:
        events.append({
            "type": "choch_bearish",
            "bias": -1,
            "level": last_swing_low,
            "strength": 0.85,
        })

    return events


# ═════════════════════════════════════════════════════════════════════════════
#  MASTER SMART MONEY ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def analyze_smart_money(df: pd.DataFrame) -> dict:
    """Run all smart money detections and return a summary."""
    result = {
        "order_blocks": find_order_blocks(df),
        "fair_value_gaps": find_fair_value_gaps(df),
        "liquidity_sweeps": find_liquidity_sweeps(df),
        "structure_breaks": detect_structure_breaks(df),
    }

    # Derive overall smart money bias
    biases = []
    for ob in result["order_blocks"]:
        biases.append(ob["bias"])
    for fvg in result["fair_value_gaps"]:
        biases.append(fvg["bias"])
    for sweep in result["liquidity_sweeps"]:
        biases.append(sweep["bias"] * 1.5)  # sweeps are high-weight
    for brk in result["structure_breaks"]:
        biases.append(brk["bias"] * (2.0 if "choch" in brk["type"] else 1.0))

    if biases:
        avg_bias = sum(biases) / len(biases)
        if avg_bias > 0.3:
            result["overall_bias"] = "BULLISH"
        elif avg_bias < -0.3:
            result["overall_bias"] = "BEARISH"
        else:
            result["overall_bias"] = "NEUTRAL"
        result["bias_score"] = round(avg_bias, 3)
    else:
        result["overall_bias"] = "NEUTRAL"
        result["bias_score"] = 0.0

    return result
