"""
Tests for grid/sizing.py — inventory scaling, depth taper, rung sizing.
"""

import pytest

from grid.sizing import inventory_scale, depth_taper, size_for_rung


class TestInventoryScale:
    def test_neutral_inventory(self):
        """With zero inventory, both sides should be 1.0."""
        assert inventory_scale("BUY", 0.0, 0.5) == 1.0
        assert inventory_scale("SELL", 0.0, 0.5) == 1.0

    def test_long_inventory_reduces_buy(self):
        """When long (inv_ratio > 0), BUY size should be reduced."""
        scale_buy = inventory_scale("BUY", 0.8, 0.5)
        assert scale_buy < 1.0

    def test_long_inventory_increases_sell(self):
        """When long, SELL size should be increased to reduce inventory."""
        scale_sell = inventory_scale("SELL", 0.8, 0.5)
        assert scale_sell > 1.0

    def test_short_inventory_reduces_sell(self):
        """When short (inv_ratio < 0), SELL size should be reduced."""
        scale_sell = inventory_scale("SELL", -0.8, 0.5)
        assert scale_sell < 1.0

    def test_max_clamp(self):
        """Scale should be clamped to [0.2, 2.0]."""
        scale = inventory_scale("BUY", 1.0, 5.0)
        assert scale >= 0.2
        scale = inventory_scale("SELL", 1.0, 5.0)
        assert scale <= 2.0

    def test_inv_ratio_clamped(self):
        """inv_ratio beyond [-1, 1] should be clamped."""
        s1 = inventory_scale("BUY", 1.0, 0.5)
        s2 = inventory_scale("BUY", 2.0, 0.5)  # 2.0 clamped to 1.0
        assert s1 == s2


class TestDepthTaper:
    def test_center_rung(self):
        """Level 1 (closest to center) should have taper = 1.0."""
        assert depth_taper(1, 0.15) == 1.0
        assert depth_taper(-1, 0.15) == 1.0

    def test_outer_rungs_reduce(self):
        """Outer rungs should have smaller taper."""
        t1 = depth_taper(1, 0.15)
        t3 = depth_taper(3, 0.15)
        t6 = depth_taper(6, 0.15)
        assert t1 > t3 > t6

    def test_zero_eta(self):
        """With eta=0, all rungs should have taper=1.0."""
        assert depth_taper(1, 0.0) == 1.0
        assert depth_taper(10, 0.0) == 1.0


class TestSizeForRung:
    def test_base_case(self):
        """At center, neutral inventory, size should equal base_size."""
        size = size_for_rung(
            side="BUY", level_index=-1, base_size=0.01,
            inv_ratio=0.0, gamma=0.5, eta=0.0,
        )
        assert abs(size - 0.01) < 1e-9

    def test_outer_rung_smaller(self):
        """Outer rung should be smaller than inner."""
        s_inner = size_for_rung(
            side="BUY", level_index=-1, base_size=0.01,
            inv_ratio=0.0, gamma=0.5, eta=0.15,
        )
        s_outer = size_for_rung(
            side="BUY", level_index=-6, base_size=0.01,
            inv_ratio=0.0, gamma=0.5, eta=0.15,
        )
        assert s_inner > s_outer

    def test_skewed_sizing(self):
        """Long inventory should reduce BUY sizes and increase SELL sizes."""
        s_buy = size_for_rung(
            side="BUY", level_index=-1, base_size=0.01,
            inv_ratio=0.8, gamma=0.5, eta=0.0,
        )
        s_sell = size_for_rung(
            side="SELL", level_index=1, base_size=0.01,
            inv_ratio=0.8, gamma=0.5, eta=0.0,
        )
        assert s_sell > s_buy

    def test_never_negative(self):
        """Size should never be negative."""
        size = size_for_rung(
            side="BUY", level_index=-1, base_size=0.01,
            inv_ratio=1.0, gamma=2.0, eta=0.5,
        )
        assert size >= 0
