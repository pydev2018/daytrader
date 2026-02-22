from strategy.types import TrendDirection
from app.main import PhasedGridEngine


def test_trend_persistence_resets_on_flat():
    assert PhasedGridEngine._trend_persistence(TrendDirection.UP, TrendDirection.FLAT, 3) == 0


def test_trend_persistence_increments_on_same_direction():
    assert PhasedGridEngine._trend_persistence(TrendDirection.DOWN, TrendDirection.DOWN, 2) == 3


def test_trend_persistence_starts_on_direction_change():
    assert PhasedGridEngine._trend_persistence(TrendDirection.UP, TrendDirection.DOWN, 6) == 1
