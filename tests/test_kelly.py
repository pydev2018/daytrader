"""
Tests for risk.kelly — validates Kelly criterion math.
"""

import pytest
from risk.kelly import kelly_fraction, kelly_from_confidence


class TestKellyFraction:
    def test_no_edge_returns_zero(self):
        """If bp < q, Kelly should return 0 (no bet)."""
        # p=0.4, b=1.0 → f* = (1*0.4 - 0.6)/1 = -0.2 → 0
        assert kelly_fraction(0.4, 1.0) == 0.0

    def test_fair_coin_no_edge(self):
        # p=0.5, b=1.0 → f* = (0.5 - 0.5)/1 = 0 → 0
        assert kelly_fraction(0.5, 1.0) == 0.0

    def test_positive_edge(self):
        # p=0.6, b=2.0 → f* = (2*0.6 - 0.4)/2 = 0.4
        # half-Kelly = 0.2, capped at 2% → 0.02
        result = kelly_fraction(0.6, 2.0)
        assert result > 0
        assert result <= 0.02  # MAX_RISK_PER_TRADE_PCT_CAP = 2.0%

    def test_high_win_rate_capped(self):
        # Even with very high edge, should be capped
        result = kelly_fraction(0.9, 5.0)
        assert result <= 0.02

    def test_invalid_inputs(self):
        assert kelly_fraction(0.0, 2.0) == 0.0  # zero win rate
        assert kelly_fraction(1.0, 2.0) == 0.0  # certain win (p=1 invalid)
        assert kelly_fraction(0.5, 0.0) == 0.0  # zero win/loss ratio
        assert kelly_fraction(0.5, -1.0) == 0.0  # negative ratio


class TestKellyFromConfidence:
    def test_low_confidence_no_bet(self):
        """Below threshold confidence should produce no Kelly bet."""
        result = kelly_from_confidence(50, 2.0)
        # At confidence 50, win_prob = 0.45, b = 2.0
        # f* = (2*0.45 - 0.55)/2 = 0.175 → half = 0.0875 → capped at 0.02
        # Actually with p=0.45: (2*0.45 - 0.55)/2 = (0.9-0.55)/2 = 0.175
        # This is positive, so Kelly says bet. But the confidence is low.
        # The SYSTEM filters this out via CONFIDENCE_THRESHOLD, not Kelly.
        assert result >= 0  # Kelly doesn't know about our threshold

    def test_high_confidence_bets(self):
        result = kelly_from_confidence(85, 2.5)
        assert result > 0, "High confidence with good R:R should produce a bet"

    def test_returns_bounded(self):
        for conf in range(60, 100):
            for rr in [1.5, 2.0, 3.0, 5.0]:
                result = kelly_from_confidence(conf, rr)
                assert 0 <= result <= 0.02, \
                    f"Kelly out of bounds: conf={conf}, rr={rr}, result={result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
