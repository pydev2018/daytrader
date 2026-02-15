"""
Tests for grid/anchor.py — EMA anchor with time-aware decay.
"""

import math
import pytest

from grid.anchor import EmaAnchor


class TestEmaAnchor:
    def test_first_update_returns_price(self):
        anchor = EmaAnchor(300.0)
        result = anchor.update(1.10000, ts=0.0)
        assert result == 1.10000

    def test_ema_moves_toward_price(self):
        anchor = EmaAnchor(300.0)
        anchor.update(1.10000, ts=0.0)
        # After a large move with enough time, anchor should start moving
        # With half_life=300, need a significant dt for visible movement
        result = anchor.update(1.11000, ts=300.0)  # One full half-life
        assert 1.10000 < result < 1.11000

    def test_longer_dt_means_more_weight(self):
        """A longer time gap should give more weight to new price."""
        a1 = EmaAnchor(300.0)
        a1.update(1.10000, ts=0.0)
        r1 = a1.update(1.11000, ts=100.0)

        a2 = EmaAnchor(300.0)
        a2.update(1.10000, ts=0.0)
        r2 = a2.update(1.11000, ts=500.0)

        # r2 should be closer to 1.11000 (more weight due to larger dt)
        assert abs(r2 - 1.11000) < abs(r1 - 1.11000)

    def test_zero_dt_returns_same(self):
        """Zero time gap should not change anchor."""
        anchor = EmaAnchor(300.0)
        anchor.update(1.10000, ts=0.0)
        result = anchor.update(1.11000, ts=0.0)
        assert result == 1.10000

    def test_halflife_semantics(self):
        """After one half-life, anchor should be roughly midway to new price.

        alpha = 1 - exp(-ln(2) * T_half / T_half) = 1 - exp(-ln2) = 0.5
        So: result = 0.5 * old + 0.5 * new = 0.5 * 1.0 + 0.5 * 2.0 = 1.5
        """
        halflife = 100.0
        anchor = EmaAnchor(halflife)
        anchor.update(1.00000, ts=100.0)  # Use nonzero start time
        result = anchor.update(2.00000, ts=100.0 + halflife)
        assert abs(result - 1.50000) < 0.01

    def test_minimum_halflife(self):
        """Half-life below 1 should be clamped to 1."""
        anchor = EmaAnchor(0.001)
        assert anchor.half_life_seconds == 1.0
