from __future__ import annotations

from dataclasses import dataclass

from strategy.types import Phase, TrendDirection


@dataclass
class GridPlan:
    long_entries: list[float]
    short_entries: list[float]
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

    long_entries = [center_price - (level + 1) * step_price for level in range(levels)]
    short_entries = [center_price + offset + level * step_price for level in range(levels)]

    if phase == Phase.TREND_LOCK:
        if trend == TrendDirection.UP:
            short_entries = []
        elif trend == TrendDirection.DOWN:
            long_entries = []

    if phase in {Phase.EXHAUSTION_CONFIRM, Phase.GARBAGE_COLLECT, Phase.RISK_OFF}:
        long_entries = []
        short_entries = []

    return GridPlan(long_entries=long_entries, short_entries=short_entries, step_price=step_price)
