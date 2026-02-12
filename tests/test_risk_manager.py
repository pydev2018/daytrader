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


class TestSymbolCooldown:
    """Tests for the symbol cooldown system (prevents re-entry churn)."""

    def test_post_loss_cooldown_blocks_reentry(self):
        """After a loss, symbol should be blocked for 4 hours."""
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
                # Record a loss on WHEAT
                rm.record_symbol_close("WHEAT", won=False, direction="BUY", entry_price=550.0)

                # Immediately try to trade WHEAT again
                allowed, reason = rm.can_open_trade("WHEAT")
                assert not allowed, "Should block WHEAT after loss"
                assert "cooldown" in reason.lower()
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state

    def test_post_win_short_cooldown(self):
        """After a win, cooldown is shorter (1 hour)."""
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
                rm.record_symbol_close("WHEAT", won=True, direction="BUY", entry_price=550.0)

                # Immediately blocked (within 1 hour)
                allowed, reason = rm.can_open_trade("WHEAT")
                assert not allowed, "Should block WHEAT immediately after win"
                assert "cooldown" in reason.lower()
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state

    def test_consecutive_losses_longer_cooldown(self):
        """2+ consecutive losses → 24-hour cooldown."""
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
                # Two losses in a row
                rm.record_symbol_close("WHEAT", won=False, direction="BUY", entry_price=550.0)
                rm.record_symbol_close("WHEAT", won=False, direction="BUY", entry_price=548.0)

                assert rm._symbol_history["WHEAT"]["consecutive_losses"] == 2

                allowed, reason = rm.can_open_trade("WHEAT")
                assert not allowed
                assert "consecutive" in reason.lower()
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state

    def test_win_resets_consecutive_losses(self):
        """A win should reset the consecutive loss counter."""
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
                rm.record_symbol_close("WHEAT", won=False, direction="BUY", entry_price=550.0)
                rm.record_symbol_close("WHEAT", won=False, direction="BUY", entry_price=548.0)
                assert rm._symbol_history["WHEAT"]["consecutive_losses"] == 2

                # Win resets counter
                rm.record_symbol_close("WHEAT", won=True, direction="BUY", entry_price=552.0)
                assert rm._symbol_history["WHEAT"]["consecutive_losses"] == 0
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state

    def test_cooldown_expired_allows_trade(self):
        """After cooldown expires, trading should be allowed again."""
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
                rm.record_symbol_close("WHEAT", won=False, direction="BUY", entry_price=550.0)

                # Simulate that 5 hours have passed (post-loss cooldown is 4h)
                past = datetime.now(timezone.utc) - timedelta(hours=5)
                rm._symbol_history["WHEAT"]["last_close_time"] = past.isoformat()

                allowed, reason = rm.can_open_trade("WHEAT")
                assert allowed, f"Should allow after cooldown expired, got: {reason}"
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state

    def test_fresh_setup_blocks_same_price_reentry(self):
        """Price must pull back 0.5 ATR before re-entering same direction."""
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
                rm.record_symbol_close("EURUSD", won=True, direction="BUY", entry_price=1.1000)

                # Expire the cooldown so that only the fresh-setup check triggers
                past = datetime.now(timezone.utc) - timedelta(hours=2)
                rm._symbol_history["EURUSD"]["last_close_time"] = past.isoformat()

                # Try to re-enter at almost the same price (ATR = 0.002)
                allowed, reason = rm.can_open_trade(
                    "EURUSD", direction="BUY", current_price=1.1003, atr=0.002
                )
                # min_pullback = 0.002 * 0.5 = 0.001, distance = 0.0003 < 0.001
                assert not allowed, f"Should block re-entry at same price, got: {reason}"
                assert "pull" in reason.lower()

                # Different direction should be fine
                allowed2, reason2 = rm.can_open_trade(
                    "EURUSD", direction="SELL", current_price=1.1003, atr=0.002
                )
                assert allowed2, f"Should allow SELL after BUY win, got: {reason2}"
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state

    def test_cooldown_persists_across_restart(self):
        """Symbol cooldown history should survive restarts."""
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
                rm1 = RiskManager(mt5)
                rm1.record_symbol_close("WHEAT", won=False, direction="BUY", entry_price=550.0)
                rm1._persist_state()

                # Simulate restart
                rm2 = RiskManager(mt5)
                assert "WHEAT" in rm2._symbol_history
                assert rm2._symbol_history["WHEAT"]["last_result"] == "loss"
                assert rm2._symbol_history["WHEAT"]["consecutive_losses"] == 1

                # Should still be blocked
                allowed, reason = rm2.can_open_trade("WHEAT")
                assert not allowed, "Cooldown should survive restart"
            finally:
                cfg.TRADE_JOURNAL_PATH = orig_journal
                cfg.BASE_DIR = orig_base
                rm_mod._RISK_STATE_PATH = orig_state


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
