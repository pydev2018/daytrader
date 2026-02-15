"""
Regime detection and grid gating.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RegimeState:
    mode: str  # ACTIVE, CAUTION, PAUSED
    trend_z: float
    vol_ratio: float
    spread_ratio: float
    reason: str = ""


def compute_trend_z(prices: list[float]) -> float:
    """Linear trend z-score of log prices."""
    if len(prices) < 10:
        return 0.0
    n = len(prices)
    xs = list(range(n))
    logs = [math.log(p) for p in prices if p > 0]
    if len(logs) != n:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(logs) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, logs))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return 0.0
    slope = num / den
    residuals = [y - (y_mean + slope * (x - x_mean)) for x, y in zip(xs, logs)]
    std = math.sqrt(sum(r * r for r in residuals) / max(1, n - 1))
    if std == 0:
        return 0.0
    return slope / std


def classify_regime(
    trend_z: float,
    vol_ratio: float,
    spread_ratio: float,
    trend_thresh: float,
    vol_thresh: float,
    spread_thresh: float,
) -> RegimeState:
    if spread_ratio >= spread_thresh:
        return RegimeState("PAUSED", trend_z, vol_ratio, spread_ratio, "spread")
    if vol_ratio >= vol_thresh:
        return RegimeState("PAUSED", trend_z, vol_ratio, spread_ratio, "vol_shock")
    if abs(trend_z) >= trend_thresh:
        return RegimeState("CAUTION", trend_z, vol_ratio, spread_ratio, "trend")
    return RegimeState("ACTIVE", trend_z, vol_ratio, spread_ratio)
