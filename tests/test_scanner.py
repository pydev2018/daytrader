"""
Tests for grid/scanner.py — symbol scoring math.
"""

import numpy as np
import pytest

from grid.scanner import _hurst_exponent, _variance_ratio


class TestHurstExponent:
    def test_random_walk(self):
        """Random walk should have H near 0.5."""
        np.random.seed(42)
        prices = 1.10000 + np.cumsum(np.random.normal(0, 0.0001, 500))
        prices = np.maximum(prices, 0.5)  # keep positive
        h = _hurst_exponent(prices)
        assert 0.35 < h < 0.75  # Broad range due to estimation noise

    def test_mean_reverting(self):
        """Mean-reverting (oscillating) prices should have H < 0.5."""
        np.random.seed(42)
        # AR(1) with negative autocorrelation
        n = 500
        prices = np.zeros(n)
        prices[0] = 1.10000
        for i in range(1, n):
            prices[i] = 1.10000 + 0.3 * (1.10000 - prices[i - 1]) + np.random.normal(0, 0.0001)
        h = _hurst_exponent(prices)
        assert h < 0.55  # Should be below 0.5 or close to it

    def test_too_few_prices(self):
        """With too few prices, should return 0.5."""
        h = _hurst_exponent(np.array([1.1, 1.2, 1.3]))
        assert h == 0.5


class TestVarianceRatio:
    def test_random_walk_near_one(self):
        """Random walk VR should be near 1.0."""
        np.random.seed(42)
        returns = np.random.normal(0, 0.001, 500)
        vr = _variance_ratio(returns, lag=5)
        assert 0.7 < vr < 1.3

    def test_mean_reverting_below_one(self):
        """Mean-reverting returns should have VR < 1."""
        np.random.seed(42)
        # Alternating returns (strongly mean-reverting)
        returns = np.array([0.001, -0.001] * 250)
        returns += np.random.normal(0, 0.0001, 500)
        vr = _variance_ratio(returns, lag=5)
        assert vr < 1.0

    def test_too_few_returns(self):
        """With too few returns, should return 1.0."""
        vr = _variance_ratio(np.array([0.001, -0.001]), lag=5)
        assert vr == 1.0
