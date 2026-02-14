"""
Utilities for M15 sniper structure, pivots, and levels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

import config as cfg
from .state import PivotPoint


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - close).abs(), (low - close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def find_pivots(df: pd.DataFrame, L: int) -> list[PivotPoint]:
    pivots: list[PivotPoint] = []
    if df is None or len(df) < (L * 2 + 3):
        return pivots

    highs = df["high"].values
    lows = df["low"].values
    times = df.index
    n = len(df)

    for i in range(L, n - L):
        hi = highs[i]
        lo = lows[i]
        if hi == np.max(highs[i - L:i + L + 1]):
            pivots.append(PivotPoint(
                type="high",
                idx=i,
                time=int(times[i].timestamp()),
                price=float(hi),
            ))
        if lo == np.min(lows[i - L:i + L + 1]):
            pivots.append(PivotPoint(
                type="low",
                idx=i,
                time=int(times[i].timestamp()),
                price=float(lo),
            ))
    return pivots


def last_swings(pivots: list[PivotPoint]) -> dict[str, list[PivotPoint]]:
    highs = [p for p in pivots if p.type == "high"]
    lows = [p for p in pivots if p.type == "low"]
    return {
        "highs": highs[-2:],
        "lows": lows[-2:],
    }


def trend_state_from_pivots(pivots: list[PivotPoint]) -> str:
    swings = last_swings(pivots)
    highs = swings["highs"]
    lows = swings["lows"]
    if len(highs) < 2 or len(lows) < 2:
        return "transition"

    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price

    if hh and hl:
        return "trend"
    if lh and ll:
        return "trend"
    if (hh and ll) or (lh and hl):
        return "transition"
    return "range"


def cluster_levels(values: Iterable[float], tol: float) -> list[list[float]]:
    levels = sorted([v for v in values if v > 0])
    clusters: list[list[float]] = []
    for v in levels:
        placed = False
        for c in clusters:
            if abs(v - np.median(c)) <= tol:
                c.append(v)
                placed = True
                break
        if not placed:
            clusters.append([v])
    return clusters


@dataclass
class RangeDetection:
    range_high: float = 0.0
    range_low: float = 0.0
    width: float = 0.0
    touch_high: int = 0
    touch_low: int = 0


def detect_range(
    pivots: list[PivotPoint],
    atr_val: float,
    lookback_bars: int,
    tol_atr: float,
) -> RangeDetection:
    if atr_val <= 0 or not pivots:
        return RangeDetection()

    recent = [p for p in pivots if p.idx >= max(0, pivots[-1].idx - lookback_bars)]
    highs = [p.price for p in recent if p.type == "high"]
    lows = [p.price for p in recent if p.type == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return RangeDetection()

    tol = atr_val * tol_atr
    high_clusters = cluster_levels(highs, tol)
    low_clusters = cluster_levels(lows, tol)

    if not high_clusters or not low_clusters:
        return RangeDetection()

    high_cluster = max(high_clusters, key=len)
    low_cluster = max(low_clusters, key=len)

    range_high = float(np.median(high_cluster))
    range_low = float(np.median(low_cluster))
    width = range_high - range_low
    return RangeDetection(
        range_high=range_high,
        range_low=range_low,
        width=width,
        touch_high=len(high_cluster),
        touch_low=len(low_cluster),
    )


def atr_percentile(atr_series: pd.Series, lookback: int) -> float:
    if atr_series is None or len(atr_series) < lookback:
        return 100.0
    window = atr_series.iloc[-lookback:]
    current = float(window.iloc[-1])
    if current <= 0:
        return 100.0
    return float(np.sum(window <= current) / len(window) * 100.0)


def major_levels_from_pivots(pivots: list[PivotPoint], atr_val: float) -> list[float]:
    if atr_val <= 0:
        return []
    levels = [p.price for p in pivots]
    clusters = cluster_levels(levels, atr_val * 0.25)
    majors = [float(np.median(c)) for c in clusters if len(c) >= 2]
    return sorted(set(majors))
