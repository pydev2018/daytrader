from strategy.planner import build_grid_plan
from strategy.types import Phase, TrendDirection


def test_range_builds_both_sides():
    plan = build_grid_plan(
        center_price=100.0,
        step_price=10.0,
        offset_ratio=0.5,
        levels=3,
        phase=Phase.RANGE,
        trend=TrendDirection.FLAT,
    )
    assert plan.long_entries == [90.0, 80.0, 70.0]
    assert plan.short_entries == [105.0, 115.0, 125.0]


def test_trend_lock_freezes_losing_side_uptrend():
    plan = build_grid_plan(
        center_price=100.0,
        step_price=10.0,
        offset_ratio=0.5,
        levels=2,
        phase=Phase.TREND_LOCK,
        trend=TrendDirection.UP,
    )
    assert plan.short_entries == []
    assert plan.long_entries == [90.0, 80.0]
