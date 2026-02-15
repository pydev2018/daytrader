"""
Tests for risk/grid_risk.py — risk controls.

Uses a mock broker to test risk logic without MT5 dependency.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from risk.grid_risk import GridRiskManager, RiskStatus


class MockBroker:
    """Minimal broker mock for risk manager testing."""

    def __init__(self, equity=10000.0, balance=10000.0, margin_level=500.0):
        self._equity = equity
        self._balance = balance
        self._margin_level = margin_level

    def account_info(self):
        return {
            "equity": self._equity,
            "balance": self._balance,
            "margin_level": self._margin_level,
        }

    def account_equity(self):
        return self._equity

    def account_balance(self):
        return self._balance


@pytest.fixture
def risk_mgr(tmp_path):
    """Create a risk manager with mocked paths and broker."""
    broker = MockBroker()
    with patch("risk.grid_risk.cfg") as mock_cfg:
        mock_cfg.RISK_STATE_PATH = tmp_path / "risk_state.json"
        mock_cfg.MAX_DRAWDOWN_PCT = 15.0
        mock_cfg.DAILY_LOSS_LIMIT_PCT = 3.0
        mock_cfg.WEEKLY_LOSS_LIMIT_PCT = 6.0
        mock_cfg.MAX_INVENTORY_LOTS = 1.0
        mock_cfg.MAX_LEVERAGE = 5.0
        mock_cfg.MAX_NOTIONAL_MULT_EQUITY = 3.0
        mgr = GridRiskManager(broker)
        yield mgr, broker, mock_cfg


class TestRiskLimits:
    def test_no_halt_under_limits(self, risk_mgr):
        mgr, broker, _ = risk_mgr
        status = mgr.check_limits(
            symbol="EURUSD",
            inventory_lots=0.5,
            notional=5000.0,
            price=1.10000,
        )
        assert not status.halted

    def test_inventory_limit_halts(self, risk_mgr):
        mgr, broker, _ = risk_mgr
        status = mgr.check_limits(
            symbol="EURUSD",
            inventory_lots=1.5,  # > MAX_INVENTORY_LOTS
            notional=5000.0,
            price=1.10000,
        )
        assert status.halted
        assert status.reason == "inventory_limit"

    def test_inventory_limit_auto_clears(self, risk_mgr):
        """Inventory halt should clear when inventory drops below limit."""
        mgr, broker, _ = risk_mgr
        # First: trigger halt
        mgr.check_limits("EURUSD", 1.5, 5000.0, 1.10000)
        assert mgr._halted

        # Then: inventory drops
        status = mgr.check_limits("EURUSD", 0.5, 5000.0, 1.10000)
        assert not status.halted

    def test_leverage_halts(self, risk_mgr):
        mgr, broker, _ = risk_mgr
        # Notional = 60000, equity = 10000 → leverage = 6 > 5
        status = mgr.check_limits(
            symbol="EURUSD",
            inventory_lots=0.5,
            notional=60000.0,
            price=1.10000,
        )
        assert status.halted
        assert status.reason == "leverage_limit"

    def test_margin_level_halts(self, risk_mgr):
        mgr, broker, _ = risk_mgr
        broker._margin_level = 150.0  # Below 200%
        status = mgr.check_limits("EURUSD", 0.1, 500.0, 1.10000)
        assert status.halted
        assert status.reason == "margin_level"

    def test_margin_level_auto_clears(self, risk_mgr):
        mgr, broker, _ = risk_mgr
        broker._margin_level = 150.0
        mgr.check_limits("EURUSD", 0.1, 500.0, 1.10000)
        assert mgr._halted

        broker._margin_level = 500.0  # Recovers
        status = mgr.check_limits("EURUSD", 0.1, 500.0, 1.10000)
        assert not status.halted

    def test_drawdown_warning_reduces_multiplier(self, risk_mgr):
        """Approaching max DD should reduce risk multiplier."""
        mgr, broker, _ = risk_mgr
        mgr._peak_equity = 10000.0
        broker._equity = 8900.0  # 11% DD, above 70% of 15% = 10.5%
        status = mgr.check_limits("EURUSD", 0.1, 500.0, 1.10000)
        assert not status.halted
        assert status.risk_multiplier < 1.0

    def test_max_drawdown_halts_permanently(self, risk_mgr):
        """Max drawdown halt should NOT auto-clear (requires manual reset)."""
        mgr, broker, _ = risk_mgr
        mgr._peak_equity = 10000.0
        broker._equity = 8400.0  # 16% DD > 15%
        mgr.check_limits("EURUSD", 0.1, 500.0, 1.10000)
        assert mgr._halted
        assert mgr._halt_reason == "max_drawdown"

        # Recovery does NOT clear max_drawdown
        broker._equity = 10000.0
        status = mgr.check_limits("EURUSD", 0.1, 500.0, 1.10000)
        assert status.halted  # Still halted!

    def test_manual_reset(self, risk_mgr):
        """Manual reset should clear any halt including max_drawdown."""
        mgr, broker, _ = risk_mgr
        mgr._halted = True
        mgr._halt_reason = "max_drawdown"
        mgr.manual_reset()
        assert not mgr._halted
        assert mgr._halt_reason == ""


class TestErrorTracking:
    def test_consecutive_errors_halt(self, risk_mgr):
        mgr, broker, _ = risk_mgr
        for _ in range(5):
            mgr.record_error()
        assert mgr._halted
        assert mgr._halt_reason == "consecutive_errors"

    def test_clear_errors(self, risk_mgr):
        mgr, broker, _ = risk_mgr
        mgr._consecutive_errors = 3
        mgr.clear_errors()
        assert mgr._consecutive_errors == 0
