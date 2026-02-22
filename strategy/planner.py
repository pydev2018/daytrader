from __future__ import annotations

from dataclasses import dataclass

from strategy.types import Phase, TrendDirection


@dataclass
class GridPlan:
    long_limits: list[float]
    long_stops: list[float]
    short_limits: list[float]
    short_stops: list[float]
    step_price: float


def build_grid_plan(
    *,
    center_price: float,
    step_price: float,
    offset_ratio: float,
    levels: int,
    phase: Phase,
    trend: TrendDirection,
) -> GridPlan:
    offset = step_price * offset_ratio

    long_limits = [center_price - (level + 1) * step_price for level in range(levels)]
    long_stops = [center_price + (level + 1) * step_price for level in range(levels)]
    
    short_limits = [center_price + offset + level * step_price for level in range(levels)]
    short_stops = [center_price - offset - level * step_price for level in range(levels)]

    if phase == Phase.TREND_LOCK:
        if trend == TrendDirection.UP:
            short_limits = []
            short_stops = []
        elif trend == TrendDirection.DOWN:
            long_limits = []
            long_stops = []

    if phase in {Phase.EXHAUSTION_CONFIRM, Phase.GARBAGE_COLLECT, Phase.RISK_OFF}:
        long_limits = []
        long_stops = []
        short_limits = []
        short_stops = []

    return GridPlan(
        long_limits=long_limits,
        long_stops=long_stops,
        short_limits=short_limits,
        short_stops=short_stops,
        step_price=step_price,
    )
