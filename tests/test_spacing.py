"""
Tests for grid/spacing.py — VolEstimator and compute_spacing.

Covers:
- VolEstimator time-aware decay
- compute_spacing cost floor, vol scaling, tick rounding
- Edge cases: zero sigma, zero tick_size, extreme prices
"""

import math
import pytest

from grid.spacing import VolEstimator, compute_spacing


class TestVolEstimator:
    def test_first_update_returns_zero(self):
        vol = VolEstimator(0.97, reference_dt=1.0)
        assert vol.update(1.10000, ts=0.0) == 0.0

    def test_returns_accumulate(self):
        vol = VolEstimator(0.97, reference_dt=1.0)
        vol.update(1.10000, ts=0.0)
        sigma = vol.update(1.10010, ts=1.0)
        assert sigma > 0

    def test_time_awareness(self):
        """Same price path, different timing — slower updates should
        produce similar vol to faster updates if time-aware."""
        # Fast updates (every 0.5s)
        vol_fast = VolEstimator(0.97, reference_dt=1.0)
        prices = [1.1000 + 0.0001 * math.sin(i * 0.1) for i in range(100)]
        for i, p in enumerate(prices):
            vol_fast.update(p, ts=i * 0.5)
        sigma_fast = vol_fast.sigma

        # Slow updates (every 2s) — same prices, just slower
        vol_slow = VolEstimator(0.97, reference_dt=1.0)
        for i, p in enumerate(prices):
            vol_slow.update(p, ts=i * 2.0)
        sigma_slow = vol_slow.sigma

        # Both should be positive and in the same order of magnitude
        assert sigma_fast > 0
        assert sigma_slow > 0
        # They won't be identical but should be same ballpark
        ratio = sigma_fast / sigma_slow
        assert 0.1 < ratio < 10.0

    def test_zero_price_returns_last_sigma(self):
        vol = VolEstimator(0.97, reference_dt=1.0)
        vol.update(1.10000, ts=0.0)
        vol.update(1.10010, ts=1.0)
        sigma_before = vol.sigma
        sigma_after = vol.update(0.0, ts=2.0)
        assert sigma_after == sigma_before

    def test_sigma_property(self):
        vol = VolEstimator(0.97, reference_dt=1.0)
        vol.update(1.10000, ts=0.0)
        vol.update(1.10010, ts=1.0)
        assert vol.sigma == math.sqrt(vol.sigma2)


class TestComputeSpacing:
    def test_minimum_ticks_floor(self):
        """Spacing should never be below min_ticks * tick_size."""
        spacing, _ = compute_spacing(
            mid=1.10000,
            spread=0.00010,
            tick_size=0.00001,
            sigma=0.0,  # zero vol → only cost/min floor
            step_seconds=1.0,
            horizon_seconds=120.0,
            k_sigma=1.2,
            k_cost=1.6,
            min_ticks=3,
            slip_ticks=1,
        )
        assert spacing >= 3 * 0.00001

    def test_cost_floor_survives(self):
        """With zero vol, spacing should be at least k_cost * cost_floor."""
        tick_size = 0.00001
        spread = 0.00020
        slip_ticks = 1
        k_cost = 1.6
        expected_cost_floor = spread + slip_ticks * tick_size
        spacing, cost_floor = compute_spacing(
            mid=1.10000,
            spread=spread,
            tick_size=tick_size,
            sigma=0.0,
            step_seconds=1.0,
            horizon_seconds=120.0,
            k_sigma=1.2,
            k_cost=k_cost,
            min_ticks=3,
            slip_ticks=slip_ticks,
        )
        assert cost_floor == expected_cost_floor
        assert spacing >= k_cost * cost_floor - tick_size  # Allow rounding

    def test_tick_rounding(self):
        """Spacing should be rounded to tick_size."""
        tick_size = 0.00001
        spacing, _ = compute_spacing(
            mid=1.10000,
            spread=0.00015,
            tick_size=tick_size,
            sigma=0.001,
            step_seconds=1.0,
            horizon_seconds=120.0,
            k_sigma=1.2,
            k_cost=1.6,
            min_ticks=3,
            slip_ticks=1,
        )
        # Check it's a multiple of tick_size (within float tolerance)
        ticks = spacing / tick_size
        assert abs(ticks - round(ticks)) < 1e-6

    def test_vol_scales_with_horizon(self):
        """Longer horizon should produce wider spacing."""
        common = dict(
            mid=1.10000,
            spread=0.00010,
            tick_size=0.00001,
            sigma=0.001,
            step_seconds=1.0,
            k_sigma=1.2,
            k_cost=1.6,
            min_ticks=3,
            slip_ticks=1,
        )
        s_short, _ = compute_spacing(horizon_seconds=30.0, **common)
        s_long, _ = compute_spacing(horizon_seconds=300.0, **common)
        assert s_long >= s_short

    def test_zero_tick_size(self):
        """Should not crash with zero tick_size."""
        spacing, _ = compute_spacing(
            mid=1.10000,
            spread=0.00010,
            tick_size=0.0,
            sigma=0.001,
            step_seconds=1.0,
            horizon_seconds=120.0,
            k_sigma=1.2,
            k_cost=1.6,
            min_ticks=3,
            slip_ticks=1,
        )
        assert spacing >= 0
