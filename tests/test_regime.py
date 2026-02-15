"""
Tests for grid/regime.py — regime detection and classification.
"""

import math
import pytest

from grid.regime import compute_trend_z, classify_regime, RegimeState


class TestComputeTrendZ:
    def test_flat_prices(self):
        """Flat prices should have trend_z near zero."""
        prices = [1.10000] * 50
        z = compute_trend_z(prices)
        assert abs(z) < 0.01

    def test_uptrend(self):
        """Steadily rising prices should have positive trend_z."""
        prices = [1.10000 + i * 0.00001 for i in range(50)]
        z = compute_trend_z(prices)
        assert z > 0

    def test_downtrend(self):
        """Steadily falling prices should have negative trend_z."""
        prices = [1.10000 - i * 0.00001 for i in range(50)]
        z = compute_trend_z(prices)
        assert z < 0

    def test_too_few_prices(self):
        """Should return 0 for fewer than 10 prices."""
        assert compute_trend_z([1.1, 1.2, 1.3]) == 0.0

    def test_single_price(self):
        assert compute_trend_z([1.1]) == 0.0

    def test_empty_list(self):
        assert compute_trend_z([]) == 0.0

    def test_strong_trend_high_z(self):
        """A very strong trend should produce a high z-score."""
        # Steep uptrend with minimal noise
        prices = [1.10000 + i * 0.001 for i in range(100)]
        z = compute_trend_z(prices)
        assert z > 2.0  # Strong positive trend


class TestClassifyRegime:
    def test_active(self):
        """Normal conditions → ACTIVE."""
        r = classify_regime(
            trend_z=0.5, vol_ratio=0.8, spread_ratio=1.0,
            trend_thresh=2.0, vol_thresh=2.0, spread_thresh=3.0,
        )
        assert r.mode == "ACTIVE"

    def test_caution_trend(self):
        """Strong trend → CAUTION."""
        r = classify_regime(
            trend_z=2.5, vol_ratio=0.8, spread_ratio=1.0,
            trend_thresh=2.0, vol_thresh=2.0, spread_thresh=3.0,
        )
        assert r.mode == "CAUTION"
        assert r.reason == "trend"

    def test_paused_vol_shock(self):
        """Vol shock → PAUSED."""
        r = classify_regime(
            trend_z=0.5, vol_ratio=2.5, spread_ratio=1.0,
            trend_thresh=2.0, vol_thresh=2.0, spread_thresh=3.0,
        )
        assert r.mode == "PAUSED"
        assert r.reason == "vol_shock"

    def test_paused_spread(self):
        """Wide spread → PAUSED (highest priority)."""
        r = classify_regime(
            trend_z=3.0, vol_ratio=3.0, spread_ratio=4.0,
            trend_thresh=2.0, vol_thresh=2.0, spread_thresh=3.0,
        )
        assert r.mode == "PAUSED"
        assert r.reason == "spread"
