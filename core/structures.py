"""
===============================================================================
  Market Structure — Support / Resistance, Supply / Demand zones
===============================================================================
  The book teaches that price is drawn like a magnet to areas of heavy
  order accumulation.  This module identifies those zones algorithmically.
===============================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as cfg
from core.patterns import find_swing_points
from utils.logger import get_logger

log = get_logger("structures")


# ═════════════════════════════════════════════════════════════════════════════
#  SUPPORT & RESISTANCE LEVELS
# ═════════════════════════════════════════════════════════════════════════════

def find_sr_levels(
    df: pd.DataFrame,
    lookback: int | None = None,
    min_touches: int | None = None,
    tolerance_pct: float | None = None,
) -> list[dict]:
    """
    Identify horizontal support / resistance levels.

    Algorithm:
    1. Find all swing highs and swing lows.
    2. Cluster nearby swings into levels.
    3. Count "touches" — the more touches, the stronger the level.
    4. Return sorted by strength.
    """
    lookback = lookback or cfg.SWING_LOOKBACK
    min_touches = min_touches or cfg.SR_MIN_TOUCHES
    tolerance_pct = tolerance_pct or cfg.SR_TOUCH_TOLERANCE_PCT

    if df is None or len(df) < lookback * 3:
        return []

    swing_highs, swing_lows = find_swing_points(df, lookback)

    # Combine all swing points
    all_points = [(t, p, "high") for t, p in swing_highs] + \
                 [(t, p, "low") for t, p in swing_lows]

    if not all_points:
        return []

    # Sort by price
    all_points.sort(key=lambda x: x[1])

    # Cluster nearby levels
    levels: list[dict] = []
    used = set()

    for i, (t_i, p_i, kind_i) in enumerate(all_points):
        if i in used:
            continue

        cluster_prices = [p_i]
        cluster_times = [t_i]
        cluster_kinds = [kind_i]
        used.add(i)

        for j in range(i + 1, len(all_points)):
            if j in used:
                continue
            t_j, p_j, kind_j = all_points[j]
            if p_i > 0 and abs(p_j - p_i) / p_i <= tolerance_pct / 100:
                cluster_prices.append(p_j)
                cluster_times.append(t_j)
                cluster_kinds.append(kind_j)
                used.add(j)

        if len(cluster_prices) >= min_touches:
            avg_price = np.mean(cluster_prices)
            # Determine if it's support, resistance, or both
            has_highs = "high" in cluster_kinds
            has_lows = "low" in cluster_kinds

            if has_highs and has_lows:
                kind = "SR"  # acts as both
            elif has_highs:
                kind = "R"
            else:
                kind = "S"

            # Recency score: more recent touches are more relevant
            recency = 0.0
            if cluster_times and df.index[-1] is not None:
                latest_touch = max(cluster_times)
                bars_ago = len(df) - df.index.get_loc(latest_touch) if latest_touch in df.index else len(df)
                recency = max(0, 1 - bars_ago / len(df))

            raw_strength = (len(cluster_prices) / 10) * 0.6 + recency * 0.4
            levels.append({
                "price": round(avg_price, 6),
                "touches": len(cluster_prices),
                "kind": kind,
                "recency": round(recency, 3),
                "strength": round(min(raw_strength, 1.0), 3),  # clamp to [0, 1]
                "first_touch": min(cluster_times),
                "last_touch": max(cluster_times),
            })

    # Sort by strength descending
    levels.sort(key=lambda x: x["strength"], reverse=True)
    return levels


# ═════════════════════════════════════════════════════════════════════════════
#  SUPPLY & DEMAND ZONES
# ═════════════════════════════════════════════════════════════════════════════

def find_supply_demand_zones(
    df: pd.DataFrame,
    min_impulse_atr: float = 2.0,
) -> list[dict]:
    """
    Find supply (bearish) and demand (bullish) zones.

    A demand zone is a consolidation area BEFORE a strong bullish impulse move.
    A supply zone is a consolidation area BEFORE a strong bearish impulse move.

    Algorithm:
    1. Detect impulsive moves (candle range > min_impulse_atr * ATR).
    2. Look back to find the consolidation area (base) before the impulse.
    3. The base high/low defines the zone boundaries.
    """
    if df is None or len(df) < 30 or "atr" not in df.columns:
        return []

    zones = []
    closes = df["close"].values
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    atrs = df["atr"].values

    for i in range(5, len(df)):
        atr_val = atrs[i]
        if np.isnan(atr_val) or atr_val == 0:
            continue

        candle_range = highs[i] - lows[i]
        body = abs(closes[i] - opens[i])

        # Is this an impulsive candle?
        if candle_range < min_impulse_atr * atr_val:
            continue
        if body < candle_range * 0.5:  # needs strong body, not just wicks
            continue

        is_bullish_impulse = closes[i] > opens[i]

        # Find the base: look back for small-bodied candles
        base_start = max(0, i - 5)
        base_end = i

        base_high = max(highs[base_start:base_end])
        base_low = min(lows[base_start:base_end])

        # Check the base had low volatility (consolidation)
        base_ranges = [highs[j] - lows[j] for j in range(base_start, base_end)]
        avg_base_range = np.mean(base_ranges) if base_ranges else 0

        if avg_base_range > atr_val * 1.5:
            continue  # not really a consolidation

        zone = {
            "type": "demand" if is_bullish_impulse else "supply",
            "bias": 1 if is_bullish_impulse else -1,
            "zone_high": round(base_high, 6),
            "zone_low": round(base_low, 6),
            "time": df.index[i],
            "impulse_strength": round(candle_range / atr_val, 2),
            "fresh": True,  # will be set to False if price returns to zone
        }

        # Check if zone has been revisited (no longer fresh)
        if i < len(df) - 1:
            future_lows = lows[i + 1:]
            future_highs = highs[i + 1:]
            if is_bullish_impulse:
                # Demand zone revisited if price drops back into it
                if len(future_lows) > 0 and min(future_lows) <= base_high:
                    zone["fresh"] = False
            else:
                # Supply zone revisited if price rises back into it
                if len(future_highs) > 0 and max(future_highs) >= base_low:
                    zone["fresh"] = False

        zones.append(zone)

    return zones


# ═════════════════════════════════════════════════════════════════════════════
#  NEAREST LEVELS TO CURRENT PRICE
# ═════════════════════════════════════════════════════════════════════════════

def nearest_sr(
    levels: list[dict],
    current_price: float,
    n: int = 3,
) -> dict:
    """
    Given S/R levels and current price, return:
    - nearest_support:    list of levels below price (sorted closest first)
    - nearest_resistance: list of levels above price (sorted closest first)
    """
    supports = sorted(
        [l for l in levels if l["price"] < current_price],
        key=lambda x: current_price - x["price"],
    )[:n]

    resistances = sorted(
        [l for l in levels if l["price"] > current_price],
        key=lambda x: x["price"] - current_price,
    )[:n]

    return {
        "nearest_support": supports,
        "nearest_resistance": resistances,
    }


def price_near_level(
    current_price: float,
    levels: list[dict],
    atr: float,
    proximity_atr: float = 1.0,
) -> list[dict]:
    """Return levels that price is currently near (within proximity_atr * ATR)."""
    near = []
    for lvl in levels:
        dist = abs(current_price - lvl["price"])
        if dist <= proximity_atr * atr:
            lvl_copy = dict(lvl)
            lvl_copy["distance"] = dist
            lvl_copy["distance_atr"] = round(dist / atr, 2) if atr > 0 else 999
            near.append(lvl_copy)
    return near


# ═════════════════════════════════════════════════════════════════════════════
#  TREND STRUCTURE (Higher Highs / Higher Lows etc.)
# ═════════════════════════════════════════════════════════════════════════════

def classify_structure(df: pd.DataFrame, lookback: int = 5) -> str:
    """
    Classify the recent market structure:
    - 'uptrend':    higher highs + higher lows
    - 'downtrend':  lower highs + lower lows
    - 'range':      no clear pattern
    """
    swing_highs, swing_lows = find_swing_points(df, lookback)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "range"

    # Check last 3-4 swings
    recent_highs = [p for _, p in swing_highs[-4:]]
    recent_lows = [p for _, p in swing_lows[-4:]]

    hh = all(recent_highs[i] > recent_highs[i - 1] for i in range(1, len(recent_highs)))
    hl = all(recent_lows[i] > recent_lows[i - 1] for i in range(1, len(recent_lows)))
    lh = all(recent_highs[i] < recent_highs[i - 1] for i in range(1, len(recent_highs)))
    ll = all(recent_lows[i] < recent_lows[i - 1] for i in range(1, len(recent_lows)))

    if hh and hl:
        return "uptrend"
    if lh and ll:
        return "downtrend"
    return "range"
