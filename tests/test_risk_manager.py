"""
Tests for risk.risk_manager — validates critical risk management fixes.
"""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class FakeMT5:
    """Mock MT5Connector for testing risk manager without MT5 terminal."""

    def __init__(self, balance=10000, equity=10000):
        self._balance = balance
        self._equity = equity
        self._positions = []

    def account_balance(self):
        return self._balance

    def account_equity(self):
        return self._equity

    def account_info(self):
        return {
            "balance": self._balance,
            "equity": self._equity,
            "margin_level": 500,
        }

    def our_positions(self):
        return self._positions


def _setup_risk_manager(mt5, tmpdir):
    """Helper: set up RiskManager with isolated state paths."""
    import config as cfg
    import risk.risk_manager as rm_mod
    cfg.TRADE_JOURNAL_PATH = Path(tmpdir) / "journal.json"
    cfg.BASE_DIR = Path(tmpdir)
    (Path(tmpdir) / "data").mkdir(exist_ok=True)
    rm_mod._RISK_STATE_PATH = Path(tmpdir) / "data" / "risk_state.json"
    return rm_mod


class TestDrawdownCheck:
    """CRITICAL: Drawdown must use equity, not balance."""

    def test_drawdown_uses_equity(self):
        """Floating losses should trigger drawdown halt."""
        from risk.risk_manager import RiskManager
        mt5 = FakeMT5(balance=10000, equity=10000)

        with tempfile.TemporaryDirectory() as tmpdir:
            import config as cfg
            orig_journal = cfg.TRADE_JOURNAL_PATH
            orig_base = cfg.BASE_DIR
            import risk.risk_manager as rm_mod
            orig_state = rm_mod._RISK_STATE_PATH

            _setup_risk_manager(mt5, tmpdir)

            try:
                rm = RiskManager(mt5)
                rm._peak_equity = 10000

                # Simulate floating loss: balance unchanged but equity dropped
                mt5._balance = 10000  # balance stays same
                mt5._equity = 8400   # 16% floating loss

                rm._check_drawdown()

                assert rm.is_halted, (
                    "CRITICAL: Drawdown check didn't halt despite 16% equity loss! "
                    "This means floating losses are invisible to risk management."
                )
                assert "max_drawdown" in rm.halt_reason
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state

    def test_drawdown_ignores_zero_equity(self):
        """If MT5 returns 0 (disconnect), drawdown check should skip, not halt."""
        from risk.risk_manager import RiskManager
        mt5 = FakeMT5(balance=0, equity=0)  # simulates disconnect

        with tempfile.TemporaryDirectory() as tmpdir:
            import config as cfg
            orig_journal = cfg.TRADE_JOURNAL_PATH
            orig_base = cfg.BASE_DIR
            import risk.risk_manager as rm_mod
            orig_state = rm_mod._RISK_STATE_PATH

            _setup_risk_manager(mt5, tmpdir)

            try:
                rm = RiskManager(mt5)
                rm._peak_equity = 10000
                rm._check_drawdown()

                assert not rm.is_halted, "Should not halt on disconnected MT5 (equity=0)"
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state


class TestDailyLimit:
    """CRITICAL: Daily limit must use day-start balance, not shrinking live balance."""

    def test_daily_limit_stable_base(self):
        """Daily limit should be computed from day-start balance, not current."""
        from risk.risk_manager import RiskManager
        mt5 = FakeMT5(balance=10000, equity=10000)

        with tempfile.TemporaryDirectory() as tmpdir:
            import config as cfg
            orig_journal = cfg.TRADE_JOURNAL_PATH
            orig_base = cfg.BASE_DIR
            import risk.risk_manager as rm_mod
            orig_state = rm_mod._RISK_STATE_PATH

            _setup_risk_manager(mt5, tmpdir)

            try:
                rm = RiskManager(mt5)
                rm._day_start_balance = 10000  # $10,000 at start of day

                # 3% of $10,000 = $300 daily limit
                # Lose $200 (should NOT halt)
                rm._daily_pnl = -200
                mt5._balance = 9800  # balance dropped
                rm._check_daily_limit()
                assert not rm.is_halted, "Should not halt at -$200 (limit is -$300)"

                # Lose $350 (should halt)
                rm._daily_pnl = -350
                rm._check_daily_limit()
                assert rm.is_halted, "Should halt at -$350 (limit is -$300)"
                assert "daily_loss_limit" in rm.halt_reason
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state


class TestStatePersistence:
    """Risk state must survive restarts."""

    def test_state_persists_and_restores(self):
        from risk.risk_manager import RiskManager
        mt5 = FakeMT5(balance=10000, equity=10000)

        with tempfile.TemporaryDirectory() as tmpdir:
            import config as cfg
            orig_journal = cfg.TRADE_JOURNAL_PATH
            orig_base = cfg.BASE_DIR
            import risk.risk_manager as rm_mod
            orig_state = rm_mod._RISK_STATE_PATH

            _setup_risk_manager(mt5, tmpdir)
            state_path = rm_mod._RISK_STATE_PATH

            try:
                # Create manager, record some activity
                rm1 = RiskManager(mt5)
                rm1._day_start_balance = 10000
                rm1.record_trade_result(-150, False)
                rm1.record_trade_result(80, True)
                rm1._persist_state()

                assert state_path.exists(), "State file should be created"

                # Simulate restart: create new RiskManager
                rm2 = RiskManager(mt5)
                assert rm2._daily_pnl == pytest.approx(-70, abs=0.01), \
                    f"Daily PnL not restored: {rm2._daily_pnl}"
                assert rm2._trades_today == 2
                assert rm2._wins_today == 1

            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state


class TestAtomicJournalWrite:
    def test_journal_write_no_corruption(self):
        from risk.risk_manager import RiskManager
        mt5 = FakeMT5()

        with tempfile.TemporaryDirectory() as tmpdir:
            import config as cfg
            orig_journal = cfg.TRADE_JOURNAL_PATH
            orig_base = cfg.BASE_DIR
            import risk.risk_manager as rm_mod
            orig_state = rm_mod._RISK_STATE_PATH

            _setup_risk_manager(mt5, tmpdir)

            try:
                rm = RiskManager(mt5)
                # Write multiple entries
                for i in range(5):
                    rm.log_trade({"action": "TEST", "index": i})

                # Verify journal is valid JSON with 5 entries
                with open(cfg.TRADE_JOURNAL_PATH) as f:
                    journal = json.load(f)
                assert len(journal) == 5
                assert journal[2]["index"] == 2
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
