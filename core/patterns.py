"""
===============================================================================
  Pattern Recognition — candlestick patterns & chart geometry
===============================================================================
  Every detector returns a label (str) and a directional bias (int):
      +1 = bullish,  -1 = bearish,  0 = neutral / continuation

  Pristine Method integration (Ch. 2, 13):
    - Candle pattern strengths are now CONTEXT-DEPENDENT.
      A hammer at a major S/R level with declining volume is strong (0.9).
      A hammer in the middle of nowhere is weak (0.4).
    - Breakout Bar Failure (BBF) detection from Ch. 13.
    - WRB/NRB/COG/Tail classification layered on top.
===============================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.logger import get_logger

log = get_logger("patterns")


# ═════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _body(row) -> float:
    return abs(row["close"] - row["open"])


def _upper_wick(row) -> float:
    return row["high"] - max(row["close"], row["open"])


def _lower_wick(row) -> float:
    return min(row["close"], row["open"]) - row["low"]


def _is_bullish(row) -> bool:
    return row["close"] > row["open"]


def _is_bearish(row) -> bool:
    return row["close"] < row["open"]


def _range_hl(row) -> float:
    return row["high"] - row["low"]


# ═════════════════════════════════════════════════════════════════════════════
#  SINGLE-CANDLE PATTERNS
# ═════════════════════════════════════════════════════════════════════════════

def detect_doji(row, threshold: float = 0.05) -> bool:
    """Body < threshold * range → doji (indecision)."""
    rng = _range_hl(row)
    return rng > 0 and _body(row) / rng < threshold


def detect_hammer(row) -> int:
    """
    Hammer / Hanging Man.
    +1 if bullish hammer (after downtrend), -1 if hanging man (after uptrend).
    Lower wick >= 2x body, small upper wick.
    """
    body = _body(row)
    lw = _lower_wick(row)
    uw = _upper_wick(row)
    if body == 0:
        return 0
    if lw >= 2 * body and uw <= body * 0.3:
        return 1  # hammer shape — context determines meaning
    return 0


def detect_inverted_hammer(row) -> int:
    """
    Inverted Hammer / Shooting Star.
    Upper wick >= 2x body, small lower wick.
    """
    body = _body(row)
    lw = _lower_wick(row)
    uw = _upper_wick(row)
    if body == 0:
        return 0
    if uw >= 2 * body and lw <= body * 0.3:
        return -1  # shooting star shape
    return 0


def detect_marubozu(row, threshold: float = 0.05) -> int:
    """Strong candle with almost no wicks."""
    rng = _range_hl(row)
    if rng == 0:
        return 0
    uw_pct = _upper_wick(row) / rng
    lw_pct = _lower_wick(row) / rng
    if uw_pct < threshold and lw_pct < threshold:
        return 1 if _is_bullish(row) else -1
    return 0


# ═════════════════════════════════════════════════════════════════════════════
#  TWO-CANDLE PATTERNS
# ═════════════════════════════════════════════════════════════════════════════

def detect_engulfing(prev, curr) -> int:
    """
    Bullish engulfing: bearish prev fully engulfed by bullish curr.
    Bearish engulfing: bullish prev fully engulfed by bearish curr.
    """
    if _is_bearish(prev) and _is_bullish(curr):
        if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
            return 1  # bullish engulfing
    if _is_bullish(prev) and _is_bearish(curr):
        if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
            return -1  # bearish engulfing
    return 0


def detect_piercing_dark_cloud(prev, curr) -> int:
    """
    Piercing Line (bullish): prev bearish, curr opens below prev low,
    closes above midpoint of prev body.
    Dark Cloud Cover (bearish): mirror.
    """
    prev_mid = (prev["open"] + prev["close"]) / 2
    if _is_bearish(prev) and _is_bullish(curr):
        if curr["open"] < prev["low"] and curr["close"] > prev_mid:
            return 1  # piercing line
    if _is_bullish(prev) and _is_bearish(curr):
        if curr["open"] > prev["high"] and curr["close"] < prev_mid:
            return -1  # dark cloud cover
    return 0


def detect_tweezer(prev, curr, tolerance: float = 0.05) -> int:
    """
    Tweezer Tops/Bottoms: two candles with nearly identical highs/lows.
    """
    avg_range = (_range_hl(prev) + _range_hl(curr)) / 2
    if avg_range == 0:
        return 0
    tol = avg_range * tolerance

    # Tweezer bottom
    if abs(prev["low"] - curr["low"]) <= tol:
        if _is_bearish(prev) and _is_bullish(curr):
            return 1
    # Tweezer top
    if abs(prev["high"] - curr["high"]) <= tol:
        if _is_bullish(prev) and _is_bearish(curr):
            return -1
    return 0


# ═════════════════════════════════════════════════════════════════════════════
#  THREE-CANDLE PATTERNS
# ═════════════════════════════════════════════════════════════════════════════

def detect_morning_evening_star(c1, c2, c3) -> int:
    """
    Morning Star (bullish): big bearish → small body → big bullish
    Evening Star (bearish): big bullish → small body → big bearish
    """
    body1 = _body(c1)
    body2 = _body(c2)
    body3 = _body(c3)

    # Small body threshold
    avg_body = (body1 + body3) / 2
    if avg_body == 0:
        return 0

    # Morning star
    if _is_bearish(c1) and body2 < avg_body * 0.3 and _is_bullish(c3):
        if c3["close"] > (c1["open"] + c1["close"]) / 2:
            return 1

    # Evening star
    if _is_bullish(c1) and body2 < avg_body * 0.3 and _is_bearish(c3):
        if c3["close"] < (c1["open"] + c1["close"]) / 2:
            return -1

    return 0


def detect_three_soldiers_crows(c1, c2, c3) -> int:
    """
    Three White Soldiers (bullish): three consecutive bullish candles, each closing higher.
    Three Black Crows (bearish): three consecutive bearish candles, each closing lower.
    """
    if all(_is_bullish(c) for c in [c1, c2, c3]):
        if c2["close"] > c1["close"] and c3["close"] > c2["close"]:
            if c2["open"] > c1["open"] and c3["open"] > c2["open"]:
                return 1

    if all(_is_bearish(c) for c in [c1, c2, c3]):
        if c2["close"] < c1["close"] and c3["close"] < c2["close"]:
            if c2["open"] < c1["open"] and c3["open"] < c2["open"]:
                return -1

    return 0


# ═════════════════════════════════════════════════════════════════════════════
#  CHART PATTERN DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def find_swing_points(df: pd.DataFrame, lookback: int = 5) -> tuple[list, list]:
    """
    Find swing highs and swing lows.
    A swing high is a bar whose high is higher than *lookback* bars on each side.
    """
    highs = []
    lows = []
    high_vals = df["high"].values
    low_vals = df["low"].values

    for i in range(lookback, len(df) - lookback):
        # Swing high
        if high_vals[i] == max(high_vals[i - lookback: i + lookback + 1]):
            highs.append((df.index[i], high_vals[i]))
        # Swing low
        if low_vals[i] == min(low_vals[i - lookback: i + lookback + 1]):
            lows.append((df.index[i], low_vals[i]))

    return highs, lows


def detect_double_top_bottom(
    swing_highs: list,
    swing_lows: list,
    tolerance: float = 0.002,
) -> list[dict]:
    """
    Detect double top / double bottom patterns from swing points.
    Returns list of pattern dicts.
    """
    patterns = []

    # Double top: two swing highs at approximately the same level
    for i in range(len(swing_highs) - 1):
        t1, h1 = swing_highs[i]
        t2, h2 = swing_highs[i + 1]
        avg = (h1 + h2) / 2
        if avg > 0 and abs(h1 - h2) / avg < tolerance:
            patterns.append({
                "type": "double_top",
                "bias": -1,
                "level": avg,
                "time1": t1,
                "time2": t2,
                "strength": 0.8,
            })

    # Double bottom: two swing lows at approximately the same level
    for i in range(len(swing_lows) - 1):
        t1, l1 = swing_lows[i]
        t2, l2 = swing_lows[i + 1]
        avg = (l1 + l2) / 2
        if avg > 0 and abs(l1 - l2) / avg < tolerance:
            patterns.append({
                "type": "double_bottom",
                "bias": 1,
                "level": avg,
                "time1": t1,
                "time2": t2,
                "strength": 0.8,
            })

    return patterns


def detect_head_shoulders(swing_highs: list, swing_lows: list) -> list[dict]:
    """
    Detect head-and-shoulders / inverse H&S from swing points.
    """
    patterns = []

    # H&S top: three swing highs where middle is highest
    for i in range(len(swing_highs) - 2):
        _, h1 = swing_highs[i]
        t2, h2 = swing_highs[i + 1]
        _, h3 = swing_highs[i + 2]
        if h2 > h1 and h2 > h3 and abs(h1 - h3) / h2 < 0.03:
            patterns.append({
                "type": "head_shoulders",
                "bias": -1,
                "level": min(h1, h3),
                "time": t2,
                "strength": 0.9,
            })

    # Inverse H&S: three swing lows where middle is lowest
    for i in range(len(swing_lows) - 2):
        _, l1 = swing_lows[i]
        t2, l2 = swing_lows[i + 1]
        _, l3 = swing_lows[i + 2]
        if l2 < l1 and l2 < l3 and abs(l1 - l3) / max(l1, l3, 1e-10) < 0.03:
            patterns.append({
                "type": "inverse_head_shoulders",
                "bias": 1,
                "level": max(l1, l3),
                "time": t2,
                "strength": 0.9,
            })

    return patterns


# ═════════════════════════════════════════════════════════════════════════════
#  MASTER PATTERN SCANNER
# ═════════════════════════════════════════════════════════════════════════════

def scan_candlestick_patterns(df: pd.DataFrame) -> list[dict]:
    """
    Scan the last few candles for candlestick patterns.
    Returns list of detected patterns with bias and strength.
    """
    if df is None or len(df) < 4:
        return []

    patterns = []
    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]

    # Single candle
    if detect_doji(last):
        patterns.append({"name": "doji", "bias": 0, "strength": 0.3})

    hammer = detect_hammer(last)
    if hammer != 0:
        # Context: is price near a low? → bullish hammer
        if last["close"] < df["close"].iloc[-20:].mean():
            patterns.append({"name": "hammer", "bias": 1, "strength": 0.7})
        else:
            patterns.append({"name": "hanging_man", "bias": -1, "strength": 0.6})

    inv_hammer = detect_inverted_hammer(last)
    if inv_hammer != 0:
        if last["close"] > df["close"].iloc[-20:].mean():
            patterns.append({"name": "shooting_star", "bias": -1, "strength": 0.7})
        else:
            patterns.append({"name": "inverted_hammer", "bias": 1, "strength": 0.6})

    maru = detect_marubozu(last)
    if maru != 0:
        patterns.append({"name": "marubozu", "bias": maru, "strength": 0.6})

    # Two candle
    engulf = detect_engulfing(prev, last)
    if engulf != 0:
        name = "bullish_engulfing" if engulf > 0 else "bearish_engulfing"
        patterns.append({"name": name, "bias": engulf, "strength": 0.85})

    pierce = detect_piercing_dark_cloud(prev, last)
    if pierce != 0:
        name = "piercing_line" if pierce > 0 else "dark_cloud_cover"
        patterns.append({"name": name, "bias": pierce, "strength": 0.7})

    tweezer = detect_tweezer(prev, last)
    if tweezer != 0:
        name = "tweezer_bottom" if tweezer > 0 else "tweezer_top"
        patterns.append({"name": name, "bias": tweezer, "strength": 0.65})

    # Three candle
    star = detect_morning_evening_star(prev2, prev, last)
    if star != 0:
        name = "morning_star" if star > 0 else "evening_star"
        patterns.append({"name": name, "bias": star, "strength": 0.9})

    soldiers = detect_three_soldiers_crows(prev2, prev, last)
    if soldiers != 0:
        name = "three_white_soldiers" if soldiers > 0 else "three_black_crows"
        patterns.append({"name": name, "bias": soldiers, "strength": 0.85})

    return patterns


def scan_chart_patterns(df: pd.DataFrame, lookback: int = 5) -> list[dict]:
    """Scan for larger chart patterns (double top/bottom, H&S)."""
    if df is None or len(df) < 30:
        return []

    swing_highs, swing_lows = find_swing_points(df, lookback)
    patterns = []
    patterns.extend(detect_double_top_bottom(swing_highs, swing_lows))
    patterns.extend(detect_head_shoulders(swing_highs, swing_lows))
    return patterns


# ═════════════════════════════════════════════════════════════════════════════
#  PRISTINE CONTEXT-AWARE PATTERN SCORING  (Ch. 2, 3, 13)
# ═════════════════════════════════════════════════════════════════════════════

def scan_patterns_with_context(
    df: pd.DataFrame,
    sr_levels: list[dict] | None = None,
    stage: dict | None = None,
    pristine_candle: dict | None = None,
) -> list[dict]:
    """
    Enhanced pattern scanner that adjusts strength based on context (Ch. 2).

    The Pristine Method says: "A hammer means nothing by itself.
    A hammer at a major support level, in a Stage 2 pullback, with declining
    volume — THAT is a high-probability signal."

    Parameters:
        df         : OHLCV DataFrame
        sr_levels  : S/R levels from structures module (for location context)
        stage      : Stage classification dict (for trend context)
        pristine_candle : last candle classification from pristine module
    """
    # Start with the base patterns
    base_patterns = scan_candlestick_patterns(df)
    if not base_patterns:
        return []

    # If we have no context, return base patterns unchanged
    if not sr_levels and not stage:
        return base_patterns

    last = df.iloc[-1]
    current_price = last["close"]

    # Estimate ATR for proximity checks
    if "atr" in df.columns:
        atr = df["atr"].iloc[-1]
        if np.isnan(atr) or atr == 0:
            atr = (df["high"] - df["low"]).iloc[-14:].mean()
    else:
        atr = (df["high"] - df["low"]).iloc[-14:].mean()

    if atr == 0:
        return base_patterns

    # Check if price is near a strong S/R level
    at_strong_sr = False
    sr_kind = ""
    if sr_levels:
        for lvl in sr_levels[:5]:  # top 5 strongest
            dist = abs(current_price - lvl.get("price", 0))
            if dist <= atr * 1.5:
                at_strong_sr = True
                sr_kind = lvl.get("kind", "")
                break

    # Check stage context
    in_tradeable_stage = False
    stage_dir = 0
    if stage:
        in_tradeable_stage = stage.get("tradeable", False)
        allowed = stage.get("allowed_direction")
        if allowed == "BUY":
            stage_dir = 1
        elif allowed == "SELL":
            stage_dir = -1

    # WRB bonus from Pristine candle classification
    is_wrb = pristine_candle.get("type") == "WRB" if pristine_candle else False
    has_tail = pristine_candle.get("tail") is not None if pristine_candle else False

    # ── Adjust strengths ─────────────────────────────────────────────────
    for pat in base_patterns:
        original_strength = pat["strength"]
        bias = pat["bias"]
        boost = 0.0
        penalty = 0.0

        # Location boost: pattern at strong S/R level
        if at_strong_sr:
            if (bias == 1 and sr_kind in ("S", "SR")) or \
               (bias == -1 and sr_kind in ("R", "SR")):
                boost += 0.15  # bullish at support or bearish at resistance
            elif (bias == 1 and sr_kind == "R") or \
                 (bias == -1 and sr_kind == "S"):
                penalty += 0.15  # bullish at resistance = bad
        else:
            penalty += 0.10  # pattern not at any S/R = weaker

        # Stage alignment boost
        if in_tradeable_stage and bias == stage_dir:
            boost += 0.10  # pattern aligns with stage direction
        elif in_tradeable_stage and bias == -stage_dir:
            penalty += 0.15  # pattern against stage = much weaker

        # WRB + pattern boost (Ch. 2 — conviction)
        if is_wrb and pristine_candle.get("bias") == bias:
            boost += 0.10

        # Tail confirmation (Ch. 2)
        if has_tail:
            if (bias == 1 and pristine_candle.get("tail") == "demand_rejection") or \
               (bias == -1 and pristine_candle.get("tail") == "supply_rejection"):
                boost += 0.05

        # Apply adjustments
        new_strength = original_strength + boost - penalty
        pat["strength"] = round(max(0.1, min(1.0, new_strength)), 2)

        # Tag with context info
        pat["at_sr"] = at_strong_sr
        pat["stage_aligned"] = in_tradeable_stage and bias == stage_dir

    return base_patterns
