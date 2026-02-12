"""
===============================================================================
  Market Structure — Support / Resistance, Supply / Demand zones
===============================================================================
  The book teaches that price is drawn like a magnet to areas of heavy
  order accumulation.  This module identifies those zones algorithmically.

  Pristine Method additions (Ch. 3):
    - Pivot-based S/R: levels derived from actual prior pivot highs/lows
    - Major vs Minor S/R classification
    - Multi-timeframe S/R aggregation
    - Price void detection
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


# ═════════════════════════════════════════════════════════════════════════════
#  MULTI-TIMEFRAME S/R AGGREGATION  (Ch. 3 — Pristine Method)
# ═════════════════════════════════════════════════════════════════════════════

# Higher-TF levels are inherently stronger
_TF_WEIGHT = {
    "W1": 5, "D1": 4, "H4": 3, "H1": 2, "M15": 1, "M5": 1,
}


def aggregate_multi_tf_sr(
    tf_levels: dict[str, list[dict]],
    current_price: float,
    cluster_pct: float = 0.3,
) -> list[dict]:
    """
    Combine S/R levels from multiple timeframes into a unified list (Ch. 3).

    The Pristine Method teaches that "the most powerful S/R levels are
    visible across multiple timeframes."  When D1, H4, and H1 all show
    a pivot at roughly the same price, that's a Major level.

    Algorithm:
        1. Collect all levels, tagging each with its source TF.
        2. Cluster nearby levels (within cluster_pct %).
        3. Multi-TF clusters get boosted strength.
        4. Tag each final level as "major" or "minor".

    Returns sorted by strength descending.
    """
    all_levels: list[dict] = []

    for tf, levels in tf_levels.items():
        weight = _TF_WEIGHT.get(tf, 1)
        for lvl in levels:
            all_levels.append({
                **lvl,
                "_tf": tf,
                "_weight": weight,
            })

    if not all_levels:
        return []

    # Sort by price
    all_levels.sort(key=lambda x: x.get("price", 0))

    # Cluster nearby levels
    clusters: list[list[dict]] = []
    used = set()

    for i, lvl_i in enumerate(all_levels):
        if i in used:
            continue
        cluster = [lvl_i]
        used.add(i)
        p_i = lvl_i.get("price", 0)
        if p_i == 0:
            continue

        for j in range(i + 1, len(all_levels)):
            if j in used:
                continue
            p_j = all_levels[j].get("price", 0)
            if p_j > 0 and abs(p_j - p_i) / p_i * 100 <= cluster_pct:
                cluster.append(all_levels[j])
                used.add(j)

        clusters.append(cluster)

    # Build unified levels from clusters
    unified: list[dict] = []

    for cluster in clusters:
        # Weighted average price
        total_w = sum(l["_weight"] for l in cluster)
        avg_price = sum(l.get("price", 0) * l["_weight"] for l in cluster) / total_w

        # Determine kind
        kinds = set(l.get("kind", "S") for l in cluster)
        if "S" in kinds and "R" in kinds:
            kind = "SR"
        elif "R" in kinds:
            kind = "R"
        else:
            kind = "S"

        # Timeframes that contributed
        contributing_tfs = list(set(l["_tf"] for l in cluster))
        max_tf_weight = max(l["_weight"] for l in cluster)

        # Strength: base from touches + TF weight boost + multi-TF bonus
        base_touches = sum(l.get("touches", 1) for l in cluster)
        multi_tf_bonus = 0.15 * (len(contributing_tfs) - 1)

        raw_strength = (
            min(base_touches / 10, 0.5)       # touches: up to 0.5
            + max_tf_weight * 0.08             # TF weight: up to 0.4
            + multi_tf_bonus                   # multi-TF: up to 0.45
        )

        # Major if: from D1/W1 OR multi-TF cluster (3+ TFs)
        major = max_tf_weight >= 4 or len(contributing_tfs) >= 3

        # Recency from the most recent contributing level
        best_recency = max(l.get("recency", 0) for l in cluster)

        unified.append({
            "price": round(avg_price, 6),
            "kind": kind,
            "strength": round(min(raw_strength, 1.0), 3),
            "touches": base_touches,
            "major": major,
            "contributing_tfs": contributing_tfs,
            "recency": best_recency,
        })

    # Sort by strength descending
    unified.sort(key=lambda x: x["strength"], reverse=True)
    return unified
