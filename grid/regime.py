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


@dataclass
class TrendModelState:
    regime: str = "RANGE"  # RANGE or TREND
    trend_score: float = 0.0
    trend_side: str = ""  # up/down
    trend_confirm: int = 0
    range_confirm: int = 0


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
    trend_pause_mult: float = 1.5,
) -> RegimeState:
    if spread_ratio >= spread_thresh:
        return RegimeState("PAUSED", trend_z, vol_ratio, spread_ratio, "spread")
    if vol_ratio >= vol_thresh:
        return RegimeState("PAUSED", trend_z, vol_ratio, spread_ratio, "vol_shock")
    if abs(trend_z) >= trend_thresh * trend_pause_mult:
        return RegimeState("PAUSED", trend_z, vol_ratio, spread_ratio, "trend_hard")
    if abs(trend_z) >= trend_thresh:
        return RegimeState("CAUTION", trend_z, vol_ratio, spread_ratio, "trend")
    return RegimeState("ACTIVE", trend_z, vol_ratio, spread_ratio)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_trend_z(prices: list[float], window: int) -> float:
    if len(prices) < max(10, window):
        return compute_trend_z(prices)
    return compute_trend_z(prices[-window:])


def _directional_persistence(prices: list[float], window: int = 20) -> float:
    if len(prices) < window + 1:
        return 0.0
    segment = prices[-(window + 1):]
    rets = []
    for prev, curr in zip(segment, segment[1:]):
        if prev <= 0 or curr <= 0:
            continue
        rets.append(math.log(curr / prev))
    if len(rets) < 6:
        return 0.0
    pos = sum(1 for r in rets if r > 0)
    neg = sum(1 for r in rets if r < 0)
    return abs(pos - neg) / max(1, len(rets))


def _breakout_z(prices: list[float], window: int = 40) -> float:
    if len(prices) < window:
        return 0.0
    segment = prices[-window:]
    mean = sum(segment) / len(segment)
    var = sum((p - mean) ** 2 for p in segment) / max(1, len(segment) - 1)
    std = math.sqrt(var)
    if std <= 1e-12:
        return 0.0
    return abs(segment[-1] - mean) / std


class TrendRegimeFilter:
    """Stateful trend detector with persistence + hysteresis.

    Produces a stable TREND/RANGE regime using:
    - Multi-horizon trend z-scores
    - Directional persistence
    - Breakout distance from local mean
    - Volatility expansion context
    """

    def __init__(
        self,
        trend_thresh: float,
        trend_pause_mult: float,
        trend_confirm_bars: int,
        range_confirm_bars: int,
    ):
        self.trend_thresh = max(1e-6, trend_thresh)
        self.trend_pause_mult = max(1.0, trend_pause_mult)
        self.trend_confirm_bars = max(1, trend_confirm_bars)
        self.range_confirm_bars = max(1, range_confirm_bars)
        self.state = TrendModelState()

    def update(
        self,
        prices: list[float],
        vol_ratio: float,
        spread_ratio: float,
        spread_thresh: float,
        vol_thresh: float,
    ) -> TrendModelState:
        if len(prices) < 15:
            return self.state

        z_fast = _safe_trend_z(prices, 24)
        z_mid = _safe_trend_z(prices, 48)
        z_slow = _safe_trend_z(prices, 96)
        abs_fast = abs(z_fast)

        persistence = _directional_persistence(prices, 20)
        breakout = _breakout_z(prices, 40)

        hard_trend = self.trend_thresh * self.trend_pause_mult
        soft_floor = self.trend_thresh * 0.6
        denom = max(1e-6, hard_trend - soft_floor)

        s_fast = _clip((abs_fast - soft_floor) / denom, 0.0, 1.0)
        s_multi = _clip((abs(z_mid) + abs(z_slow)) / max(1e-6, 2 * hard_trend), 0.0, 1.0)
        s_persist = _clip(persistence, 0.0, 1.0)
        s_breakout = _clip((breakout - 1.0) / 2.0, 0.0, 1.0)
        vol_denom = max(1e-6, vol_thresh - 1.0)
        s_vol = _clip((vol_ratio - 1.0) / vol_denom, 0.0, 1.0)
        s_spread_penalty = _clip((spread_ratio - spread_thresh) / max(1.0, spread_thresh), 0.0, 1.0)

        trend_score = (
            0.34 * s_fast
            + 0.22 * s_multi
            + 0.20 * s_persist
            + 0.16 * s_breakout
            + 0.08 * s_vol
            - 0.10 * s_spread_penalty
        )
        trend_score = _clip(trend_score, 0.0, 1.0)

        enter_signal = trend_score >= 0.72 or abs_fast >= hard_trend
        exit_signal = trend_score <= 0.38 and abs_fast <= self.trend_thresh * 0.8

        if enter_signal:
            self.state.trend_confirm += 1
            self.state.range_confirm = 0
        elif exit_signal:
            self.state.range_confirm += 1
            self.state.trend_confirm = 0
        else:
            self.state.trend_confirm = max(0, self.state.trend_confirm - 1)
            self.state.range_confirm = max(0, self.state.range_confirm - 1)

        if self.state.regime != "TREND" and self.state.trend_confirm >= self.trend_confirm_bars:
            self.state.regime = "TREND"
        elif self.state.regime == "TREND" and self.state.range_confirm >= self.range_confirm_bars:
            self.state.regime = "RANGE"

        self.state.trend_score = trend_score
        if z_fast > 0:
            self.state.trend_side = "up"
        elif z_fast < 0:
            self.state.trend_side = "down"
        else:
            self.state.trend_side = ""

        return self.state
