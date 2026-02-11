"""
Tests for utils.market_hours — validates session logic and Friday close fix.
"""

from datetime import datetime, timezone
import pytest
from utils.market_hours import is_market_open, active_sessions, session_score


class TestMarketOpen:
    def test_monday_morning_open(self):
        dt = datetime(2025, 2, 10, 8, 0, tzinfo=timezone.utc)  # Monday 8 AM
        assert is_market_open(dt) is True

    def test_friday_20_open(self):
        dt = datetime(2025, 2, 14, 20, 0, tzinfo=timezone.utc)  # Friday 8 PM
        assert is_market_open(dt) is True

    def test_friday_21_closed(self):
        """FIXED: Market should close at 21:00 UTC Friday, not 22:00."""
        dt = datetime(2025, 2, 14, 21, 30, tzinfo=timezone.utc)  # Friday 9:30 PM
        assert is_market_open(dt) is False, "Market should be CLOSED at 21:30 UTC Friday"

    def test_saturday_closed(self):
        dt = datetime(2025, 2, 15, 12, 0, tzinfo=timezone.utc)  # Saturday noon
        assert is_market_open(dt) is False

    def test_sunday_before_21_closed(self):
        dt = datetime(2025, 2, 16, 20, 0, tzinfo=timezone.utc)  # Sunday 8 PM
        assert is_market_open(dt) is False

    def test_sunday_after_21_open(self):
        dt = datetime(2025, 2, 16, 21, 30, tzinfo=timezone.utc)  # Sunday 9:30 PM
        assert is_market_open(dt) is True


class TestActiveSessions:
    def test_london_session(self):
        dt = datetime(2025, 2, 10, 10, 0, tzinfo=timezone.utc)  # Monday 10 AM
        sessions = active_sessions(dt)
        assert "London" in sessions

    def test_overlap_london_newyork(self):
        dt = datetime(2025, 2, 10, 14, 0, tzinfo=timezone.utc)  # Monday 2 PM
        sessions = active_sessions(dt)
        assert "London" in sessions
        assert "NewYork" in sessions

    def test_sydney_wraps_midnight(self):
        dt = datetime(2025, 2, 10, 22, 0, tzinfo=timezone.utc)  # Monday 10 PM
        sessions = active_sessions(dt)
        assert "Sydney" in sessions


class TestSessionScore:
    def test_eurusd_during_london(self):
        dt = datetime(2025, 2, 10, 10, 0, tzinfo=timezone.utc)
        score = session_score("EURUSD.sml", dt)
        assert score >= 0.7, f"EUR during London should score well: {score}"

    def test_audusd_during_sydney(self):
        dt = datetime(2025, 2, 10, 23, 0, tzinfo=timezone.utc)  # Sydney session
        score = session_score("AUDUSD.sml", dt)
        assert score >= 0.7, f"AUD during Sydney should score well: {score}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
