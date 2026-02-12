"""
===============================================================================
  Risk Manager — The Guardian
===============================================================================
  Enforces every risk rule.  No exceptions.  No overrides.
  - Daily loss limit
  - Weekly loss limit
  - Max drawdown protection (uses EQUITY, not balance)
  - Max concurrent positions
  - Correlation limits
  - Per-trade risk caps
  - Persists state to disk so limits survive restarts
===============================================================================
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import config as cfg
from core.mt5_connector import MT5Connector
from utils.logger import get_logger

log = get_logger("risk_mgr")

# Path for persisted risk state (survives restarts)
_RISK_STATE_PATH = cfg.BASE_DIR / "data" / "risk_state.json"


class RiskManager:
    """Stateful risk manager that tracks PnL and enforces limits."""

    def __init__(self, mt5_conn: MT5Connector):
        self.mt5 = mt5_conn
        self._daily_pnl: float = 0.0
        self._weekly_pnl: float = 0.0
        self._trades_today: int = 0
        self._wins_today: int = 0
        self._day_start: datetime = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self._week_start: datetime = self._day_start - timedelta(
            days=self._day_start.weekday()
        )
        # Initialise peak equity and balance from LIVE account, not config
        _live_equity = self.mt5.account_equity()
        _live_balance = self.mt5.account_balance()
        self._peak_equity: float = _live_equity if _live_equity > 0 else cfg.TRADING_CAPITAL
        # Snapshot of balance at start of day — used as stable base for limits
        self._day_start_balance: float = _live_balance if _live_balance > 0 else cfg.TRADING_CAPITAL
        self._week_start_balance: float = _live_balance if _live_balance > 0 else cfg.TRADING_CAPITAL
        self._halted: bool = False
        self._halt_reason: str = ""

        # ── Symbol cooldown tracking ─────────────────────────────────────
        # Maps symbol → {"last_close_time": datetime, "last_result": "win"/"loss",
        #                 "last_direction": "BUY"/"SELL", "last_entry_price": float,
        #                 "consecutive_losses": int}
        self._symbol_history: dict[str, dict] = {}

        self._journal_path = cfg.TRADE_JOURNAL_PATH
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        _RISK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Restore state from disk (survives restart)
        self._restore_state()

    # =====================================================================
    #  STATE PERSISTENCE (survives restarts)
    # =====================================================================

    def _persist_state(self):
        """Atomically write risk state to disk."""
        state = {
            "daily_pnl": self._daily_pnl,
            "weekly_pnl": self._weekly_pnl,
            "trades_today": self._trades_today,
            "wins_today": self._wins_today,
            "day_start": self._day_start.isoformat(),
            "week_start": self._week_start.isoformat(),
            "peak_equity": self._peak_equity,
            "day_start_balance": self._day_start_balance,
            "week_start_balance": self._week_start_balance,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "symbol_history": self._symbol_history,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            # Atomic write: write to temp file then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=_RISK_STATE_PATH.parent, suffix=".tmp"
            )
            with os.fdopen(fd, "w") as f:
                json.dump(state, f, indent=2, default=str)
            # os.replace is atomic on Windows (single syscall) — no delete gap
            os.replace(tmp_path, _RISK_STATE_PATH)
        except Exception as e:
            log.warning(f"Failed to persist risk state: {e}")

    def _restore_state(self):
        """Restore risk state from disk if it's from the same day/week."""
        if not _RISK_STATE_PATH.exists():
            return
        try:
            with open(_RISK_STATE_PATH, "r") as f:
                state = json.load(f)

            saved_day_start = datetime.fromisoformat(state["day_start"])
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            # Only restore daily state if it's from today
            if saved_day_start.date() == today_start.date():
                self._daily_pnl = state.get("daily_pnl", 0.0)
                self._trades_today = state.get("trades_today", 0)
                self._wins_today = state.get("wins_today", 0)
                self._day_start_balance = state.get(
                    "day_start_balance", cfg.TRADING_CAPITAL
                )
                log.info(
                    f"Restored daily risk state: PnL=${self._daily_pnl:.2f}, "
                    f"trades={self._trades_today}"
                )
            else:
                log.info("Saved risk state is from a previous day — starting fresh")

            # Restore weekly state if same week
            saved_week_start = datetime.fromisoformat(state["week_start"])
            current_week_start = today_start - timedelta(days=today_start.weekday())
            if saved_week_start.date() >= current_week_start.date():
                self._weekly_pnl = state.get("weekly_pnl", 0.0)
                self._week_start_balance = state.get(
                    "week_start_balance", cfg.TRADING_CAPITAL
                )

            # Always restore peak, halt state, and symbol cooldown history
            self._peak_equity = state.get("peak_equity", cfg.TRADING_CAPITAL)
            self._halted = state.get("halted", False)
            self._halt_reason = state.get("halt_reason", "")
            self._symbol_history = state.get("symbol_history", {})

            # ── CRITICAL: Clear stale halts that no longer apply ─────────
            # A daily halt from yesterday must not block today's trading.
            if self._halted and self._halt_reason == "daily_loss_limit":
                if saved_day_start.date() != today_start.date():
                    self._halted = False
                    self._halt_reason = ""
                    log.info(
                        "Cleared stale daily_loss_limit halt from previous day"
                    )

            if self._halted and self._halt_reason == "weekly_loss_limit":
                if saved_week_start.date() < current_week_start.date():
                    self._halted = False
                    self._halt_reason = ""
                    log.info(
                        "Cleared stale weekly_loss_limit halt from previous week"
                    )

            if self._halted:
                log.warning(
                    f"Restored HALTED state: {self._halt_reason} — "
                    "trading will NOT resume automatically"
                )

        except Exception as e:
            log.warning(f"Failed to restore risk state: {e}")

    # =====================================================================
    #  STATE MANAGEMENT
    # =====================================================================

    def update_peak_equity(self, current_equity: float):
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

    def record_trade_result(self, pnl: float, won: bool):
        """Called after every trade close."""
        self._daily_pnl += pnl
        self._weekly_pnl += pnl
        self._trades_today += 1
        if won:
            self._wins_today += 1

        # Check limits after each trade
        self._check_daily_limit()
        self._check_weekly_limit()
        self._check_drawdown()

        # Persist state so limits survive restarts
        self._persist_state()

    def reset_daily(self):
        """Called at start of new trading day."""
        self._daily_pnl = 0.0
        self._trades_today = 0
        self._wins_today = 0
        self._day_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # Snapshot today's starting balance for stable limit calculation
        balance = self.mt5.account_balance()
        if balance > 0:
            self._day_start_balance = balance
        # Don't reset halt if it's a drawdown halt
        if self._halt_reason == "daily_loss_limit":
            self._halted = False
            self._halt_reason = ""
            log.info("Daily loss limit reset — trading resumed")
        self._persist_state()

    def reset_weekly(self):
        """Called at start of new trading week."""
        self._weekly_pnl = 0.0
        self._week_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        balance = self.mt5.account_balance()
        if balance > 0:
            self._week_start_balance = balance
        if self._halt_reason == "weekly_loss_limit":
            self._halted = False
            self._halt_reason = ""
            log.info("Weekly loss limit reset — trading resumed")
        self._persist_state()

    # =====================================================================
    #  RISK CHECKS
    # =====================================================================

    def _check_daily_limit(self):
        # FIXED: Use day-start balance (stable), not live balance (shrinking)
        capital_base = max(self._day_start_balance, 1.0)
        daily_limit = capital_base * (cfg.DAILY_LOSS_LIMIT_PCT / 100)
        if self._daily_pnl < -daily_limit:
            self._halted = True
            self._halt_reason = "daily_loss_limit"
            log.warning(
                f"DAILY LOSS LIMIT HIT: ${self._daily_pnl:.2f} "
                f"(limit: -${daily_limit:.2f} of day-start ${capital_base:.2f}) "
                f"— HALTING"
            )

    def _check_weekly_limit(self):
        # FIXED: Use week-start balance (stable)
        capital_base = max(self._week_start_balance, 1.0)
        weekly_limit = capital_base * (cfg.WEEKLY_LOSS_LIMIT_PCT / 100)
        if self._weekly_pnl < -weekly_limit:
            self._halted = True
            self._halt_reason = "weekly_loss_limit"
            log.warning(
                f"WEEKLY LOSS LIMIT HIT: ${self._weekly_pnl:.2f} "
                f"(limit: -${weekly_limit:.2f}) — HALTING"
            )

    def _check_drawdown(self):
        # FIXED: Use EQUITY (includes floating PnL), not balance
        current_equity = self.mt5.account_equity()
        # Guard: if MT5 returns 0 (disconnected), skip the check entirely
        if current_equity <= 0:
            log.warning(
                "account_equity() returned 0 — skipping drawdown check "
                "(possible disconnect)"
            )
            return
        self.update_peak_equity(current_equity)
        if self._peak_equity > 0:
            dd = (self._peak_equity - current_equity) / self._peak_equity * 100
            if dd >= cfg.MAX_DRAWDOWN_PCT:
                self._halted = True
                self._halt_reason = "max_drawdown"
                log.error(
                    f"MAX DRAWDOWN HIT: {dd:.1f}% "
                    f"(peak equity: ${self._peak_equity:.2f}, "
                    f"current equity: ${current_equity:.2f}) "
                    f"— HALTING ALL TRADING"
                )
                self._persist_state()

    def periodic_risk_check(self):
        """Run periodic risk checks (call from main loop, not just on trade close)."""
        self._check_drawdown()

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    # =====================================================================
    #  SYMBOL COOLDOWN — prevents re-entry churn
    # =====================================================================

    def record_symbol_close(
        self, symbol: str, won: bool, direction: str, entry_price: float
    ):
        """
        Called when a position on *symbol* closes.  Records the result so
        that can_open_trade() can enforce cooldown periods.

        Cooldown logic (from a quant perspective):
        - After SL (loss): wait 4 hours minimum.  The higher-TF trend that
          generated this signal hasn't changed yet; re-entering immediately
          repeats the same losing setup.
        - After TP (win):  wait 1 hour minimum.  The trend may continue, but
          require a fresh pullback / new pattern, not the same stale signal.
        - After 2+ consecutive losses on same symbol: wait 24 hours.
          At that point the thesis is broken.
        """
        hist = self._symbol_history.get(symbol, {})
        prev_consec = hist.get("consecutive_losses", 0)

        self._symbol_history[symbol] = {
            "last_close_time": datetime.now(timezone.utc).isoformat(),
            "last_result": "win" if won else "loss",
            "last_direction": direction,
            "last_entry_price": entry_price,
            "consecutive_losses": 0 if won else prev_consec + 1,
        }
        log.info(
            f"{symbol}: recorded {'WIN' if won else 'LOSS'} "
            f"(consecutive losses: {self._symbol_history[symbol]['consecutive_losses']})"
        )
        self._persist_state()  # cooldowns must survive crashes

    def _check_symbol_cooldown(self, symbol: str) -> tuple[bool, str]:
        """
        Check if *symbol* is in cooldown after a recent close.
        Returns (allowed, reason).
        """
        hist = self._symbol_history.get(symbol)
        if hist is None:
            return True, "OK"  # never traded this symbol

        try:
            last_close = datetime.fromisoformat(hist["last_close_time"])
        except (KeyError, ValueError):
            return True, "OK"

        now = datetime.now(timezone.utc)
        hours_since = (now - last_close).total_seconds() / 3600
        consec_losses = hist.get("consecutive_losses", 0)

        # 2+ consecutive losses → 24-hour cooldown (thesis is broken)
        if consec_losses >= 2:
            if hours_since < 24:
                remaining = 24 - hours_since
                return False, (
                    f"{symbol}: {consec_losses} consecutive losses — "
                    f"cooldown {remaining:.1f}h remaining"
                )

        # After a loss → 4-hour cooldown
        if hist.get("last_result") == "loss":
            if hours_since < 4:
                remaining = 4 - hours_since
                return False, (
                    f"{symbol}: post-loss cooldown — {remaining:.1f}h remaining"
                )

        # After a win → 1-hour cooldown (don't immediately chase)
        if hist.get("last_result") == "win":
            if hours_since < 1:
                remaining = 1 - hours_since
                return False, (
                    f"{symbol}: post-win cooldown — {remaining:.1f}h remaining"
                )

        return True, "OK"

    def get_symbol_history(self, symbol: str) -> Optional[dict]:
        """Return cooldown history for a symbol (used by confluence engine)."""
        return self._symbol_history.get(symbol)

    def can_open_trade(
        self,
        symbol: str,
        direction: Optional[str] = None,
        current_price: float = 0.0,
        atr: float = 0.0,
    ) -> tuple[bool, str]:
        """
        Master gate: check ALL risk conditions before allowing a trade.
        Returns (allowed: bool, reason: str).

        Optional params for the "fresh setup" check:
          direction    — "BUY" or "SELL"
          current_price — live price of the instrument
          atr          — current H1 ATR
        """
        # ── Halt check ───────────────────────────────────────────────────
        if self._halted:
            return False, f"Trading halted: {self._halt_reason}"

        # ── Max concurrent positions ─────────────────────────────────────
        our_positions = self.mt5.our_positions()
        if len(our_positions) >= cfg.MAX_CONCURRENT_POSITIONS:
            return False, f"Max concurrent positions ({cfg.MAX_CONCURRENT_POSITIONS}) reached"

        # ── Already in this symbol? ──────────────────────────────────────
        for pos in our_positions:
            if pos.get("symbol") == symbol:
                return False, f"Already have a position in {symbol}"

        # ── Symbol cooldown (post-SL / post-TP) ─────────────────────────
        allowed, reason = self._check_symbol_cooldown(symbol)
        if not allowed:
            return False, reason

        # ── Fresh setup requirement ──────────────────────────────────────
        # If we recently traded this symbol in the SAME direction, require
        # that price has pulled back at least 0.5 ATR from the last entry.
        # This prevents entering at the exact same price repeatedly.
        if direction and current_price > 0 and atr > 0:
            hist = self._symbol_history.get(symbol)
            if hist and hist.get("last_direction") == direction:
                last_entry = hist.get("last_entry_price", 0)
                if last_entry > 0:
                    distance = abs(current_price - last_entry)
                    min_pullback = atr * 0.5
                    if distance < min_pullback:
                        return False, (
                            f"{symbol}: price hasn't pulled back enough from "
                            f"last entry ({last_entry:.5f}) — "
                            f"need {min_pullback:.5f} movement, "
                            f"only {distance:.5f} so far"
                        )

        # ── Correlation check ────────────────────────────────────────────
        # Use substring matching: Oanda symbols have suffixes (e.g. EURUSD.sml)
        open_symbols = {pos.get("symbol", "") for pos in our_positions}
        correlated_count = 0
        sym_upper = symbol.upper()
        for group in cfg.CORRELATION_GROUPS:
            symbol_in_group = any(member.upper() in sym_upper for member in group)
            if symbol_in_group:
                for open_sym in open_symbols:
                    open_upper = open_sym.upper()
                    if any(member.upper() in open_upper for member in group):
                        correlated_count += 1
        if correlated_count >= cfg.MAX_CORRELATED_POSITIONS:
            return False, f"Max correlated positions ({cfg.MAX_CORRELATED_POSITIONS}) for {symbol}'s group"

        # ── Margin check ─────────────────────────────────────────────────
        acc = self.mt5.account_info()
        margin_level = acc.get("margin_level", 0)
        if margin_level > 0 and margin_level < 200:  # < 200% margin level is risky
            return False, f"Margin level too low: {margin_level:.1f}%"

        return True, "OK"

    def clear_halt(self, confirm: str = ""):
        """
        Manual admin method to clear a halt (e.g., after max_drawdown).
        Requires explicit confirmation string to prevent accidental calls.
        """
        if confirm != "I_ACCEPT_THE_RISK":
            log.warning("clear_halt called without proper confirmation — ignored")
            return False
        prev_reason = self._halt_reason
        self._halted = False
        self._halt_reason = ""
        self._persist_state()
        log.warning(f"HALT MANUALLY CLEARED (was: {prev_reason})")
        return True

    # =====================================================================
    #  RISK-ADJUSTED SIZING
    # =====================================================================

    def adjusted_risk_pct(self) -> float:
        """
        Return the current risk % per trade, reduced if we're in drawdown
        or approaching daily/weekly limits.
        Uses the WORST (most conservative) reduction — not multiplicative.
        """
        base = cfg.MAX_RISK_PER_TRADE_PCT
        worst_multiplier = 1.0

        # FIXED: Use day-start balance (stable) for limit calculations
        daily_base = max(self._day_start_balance, 1.0)
        weekly_base = max(self._week_start_balance, 1.0)

        # Reduce risk after daily losses
        if self._daily_pnl < 0:
            daily_limit = daily_base * (cfg.DAILY_LOSS_LIMIT_PCT / 100)
            if daily_limit > 0:
                loss_ratio = abs(self._daily_pnl) / daily_limit
                if loss_ratio > 0.5:
                    worst_multiplier = min(worst_multiplier, 0.5)

        # Reduce risk after weekly losses
        if self._weekly_pnl < 0:
            weekly_limit = weekly_base * (cfg.WEEKLY_LOSS_LIMIT_PCT / 100)
            if weekly_limit > 0:
                loss_ratio = abs(self._weekly_pnl) / weekly_limit
                if loss_ratio > 0.5:
                    worst_multiplier = min(worst_multiplier, 0.5)

        return max(base * worst_multiplier, 0.25)  # minimum 0.25%

    # =====================================================================
    #  TRADE JOURNAL (atomic writes)
    # =====================================================================

    def log_trade(self, trade_data: dict):
        """Append a trade entry to the JSON journal. Uses atomic write."""
        journal = []
        if self._journal_path.exists():
            try:
                with open(self._journal_path, "r") as f:
                    journal = json.load(f)
            except (json.JSONDecodeError, IOError):
                journal = []

        trade_data["timestamp"] = datetime.now(timezone.utc).isoformat()
        journal.append(trade_data)

        # Atomic write: temp file → rename
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=self._journal_path.parent, suffix=".tmp"
            )
            with os.fdopen(fd, "w") as f:
                json.dump(journal, f, indent=2, default=str)
            # os.replace is atomic on Windows — no delete gap where crash loses both files
            os.replace(tmp_path, self._journal_path)
        except Exception as e:
            log.error(f"Failed to write trade journal: {e}")

    # =====================================================================
    #  STATS
    # =====================================================================

    @property
    def daily_stats(self) -> dict:
        return {
            "daily_pnl": self._daily_pnl,
            "weekly_pnl": self._weekly_pnl,
            "trades_today": self._trades_today,
            "wins_today": self._wins_today,
            "win_rate": (
                self._wins_today / self._trades_today
                if self._trades_today > 0 else 0
            ),
            "peak_equity": self._peak_equity,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
        }
