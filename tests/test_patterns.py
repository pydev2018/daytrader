"""
Tests for core.patterns — validates candlestick pattern detection.
"""

import pytest
from core.patterns import (
    detect_engulfing,
    detect_tweezer,
    detect_hammer,
    detect_morning_evening_star,
    detect_doji,
)


def _candle(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


class TestEngulfing:
    def test_bullish_engulfing(self):
        prev = _candle(1.10, 1.105, 1.09, 1.095)  # bearish
        curr = _candle(1.093, 1.11, 1.092, 1.107)  # bullish engulfs
        assert detect_engulfing(prev, curr) == 1

    def test_bearish_engulfing(self):
        prev = _candle(1.09, 1.105, 1.088, 1.10)   # bullish
        curr = _candle(1.102, 1.103, 1.08, 1.085)   # bearish engulfs
        assert detect_engulfing(prev, curr) == -1

    def test_no_engulfing(self):
        prev = _candle(1.10, 1.105, 1.095, 1.097)
        curr = _candle(1.098, 1.102, 1.096, 1.101)  # doesn't fully engulf
        assert detect_engulfing(prev, curr) == 0


class TestTweezer:
    def test_tweezer_bottom_with_fixed_tolerance(self):
        """FIXED: tolerance was 0.001 (too tight), now 0.05."""
        prev = _candle(1.10, 1.105, 1.090, 1.092)   # bearish, low at 1.090
        curr = _candle(1.092, 1.102, 1.0905, 1.100)  # bullish, low at 1.0905
        # Difference = 0.0005, avg_range = (0.015 + 0.0115)/2 ≈ 0.01325
        # tolerance * avg_range = 0.05 * 0.01325 ≈ 0.000663
        # 0.0005 < 0.000663 → should detect
        result = detect_tweezer(prev, curr)
        assert result == 1, f"Should detect tweezer bottom, got {result}"


class TestHammer:
    def test_hammer_shape(self):
        # Long lower wick, small body, upper wick < 30% of body
        # body = |1.101 - 1.100| = 0.001
        # lower_wick = min(open,close) - low = 1.100 - 1.090 = 0.010  (>= 2*body ✓)
        # upper_wick = high - max(open,close) = 1.10125 - 1.101 = 0.00025  (<= 0.3*body ✓)
        candle = _candle(1.100, 1.10125, 1.090, 1.101)
        result = detect_hammer(candle)
        assert result == 1


class TestDoji:
    def test_doji_detected(self):
        candle = _candle(1.100, 1.110, 1.090, 1.1005)  # body ≈ 0.0005, range = 0.02
        assert detect_doji(candle) is True

    def test_normal_candle_not_doji(self):
        candle = _candle(1.090, 1.110, 1.085, 1.105)  # body = 0.015, range = 0.025
        assert detect_doji(candle) is False


class TestMorningEveningStar:
    def test_morning_star(self):
        c1 = _candle(1.10, 1.105, 1.08, 1.085)  # big bearish
        c2 = _candle(1.084, 1.086, 1.083, 1.085)  # small body (doji-like)
        c3 = _candle(1.086, 1.105, 1.085, 1.10)   # big bullish, closes above c1 midpoint
        result = detect_morning_evening_star(c1, c2, c3)
        assert result == 1

    def test_evening_star(self):
        c1 = _candle(1.08, 1.10, 1.078, 1.098)   # big bullish
        c2 = _candle(1.099, 1.101, 1.098, 1.099)  # small body
        c3 = _candle(1.098, 1.099, 1.075, 1.08)   # big bearish, closes below c1 midpoint
        result = detect_morning_evening_star(c1, c2, c3)
        assert result == -1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
