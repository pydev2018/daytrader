"""
===============================================================================
  Risk Manager — The Guardian
===============================================================================
  Enforces every risk rule.  No exceptions.  No overrides.
  - Daily loss limit
  - Weekly loss limit
  - Max drawdown protection
  - Max concurrent positions
  - Correlation limits
  - Per-trade risk caps
===============================================================================
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import config as cfg
from core.mt5_connector import MT5Connector
from utils.logger import get_logger

log = get_logger("risk_mgr")


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
        self._peak_balance: float = cfg.TRADING_CAPITAL
        self._halted: bool = False
        self._halt_reason: str = ""

        self._journal_path = cfg.TRADE_JOURNAL_PATH
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)

    # =====================================================================
    #  STATE MANAGEMENT
    # =====================================================================

    def update_peak_balance(self, current_balance: float):
        if current_balance > self._peak_balance:
            self._peak_balance = current_balance

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

    def reset_daily(self):
        """Called at start of new trading day."""
        self._daily_pnl = 0.0
        self._trades_today = 0
        self._wins_today = 0
        self._day_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # Don't reset halt if it's a drawdown halt
        if self._halt_reason == "daily_loss_limit":
            self._halted = False
            self._halt_reason = ""
            log.info("Daily loss limit reset — trading resumed")

    def reset_weekly(self):
        """Called at start of new trading week."""
        self._weekly_pnl = 0.0
        self._week_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if self._halt_reason == "weekly_loss_limit":
            self._halted = False
            self._halt_reason = ""
            log.info("Weekly loss limit reset — trading resumed")

    # =====================================================================
    #  RISK CHECKS
    # =====================================================================

    def _check_daily_limit(self):
        # Use live equity (or peak balance) for dynamic limit — protects shrinking accounts
        capital_base = max(self.mt5.account_balance(), 1.0)
        daily_limit = capital_base * (cfg.DAILY_LOSS_LIMIT_PCT / 100)
        if self._daily_pnl < -daily_limit:
            self._halted = True
            self._halt_reason = "daily_loss_limit"
            log.warning(
                f"DAILY LOSS LIMIT HIT: ${self._daily_pnl:.2f} "
                f"(limit: -${daily_limit:.2f}) — HALTING"
            )

    def _check_weekly_limit(self):
        capital_base = max(self.mt5.account_balance(), 1.0)
        weekly_limit = capital_base * (cfg.WEEKLY_LOSS_LIMIT_PCT / 100)
        if self._weekly_pnl < -weekly_limit:
            self._halted = True
            self._halt_reason = "weekly_loss_limit"
            log.warning(
                f"WEEKLY LOSS LIMIT HIT: ${self._weekly_pnl:.2f} "
                f"(limit: -${weekly_limit:.2f}) — HALTING"
            )

    def _check_drawdown(self):
        current_balance = self.mt5.account_balance()
        # Guard: if MT5 returns 0 (disconnected), skip the check entirely
        if current_balance <= 0:
            log.warning("account_balance() returned 0 — skipping drawdown check (possible disconnect)")
            return
        self.update_peak_balance(current_balance)
        if self._peak_balance > 0:
            dd = (self._peak_balance - current_balance) / self._peak_balance * 100
            if dd >= cfg.MAX_DRAWDOWN_PCT:
                self._halted = True
                self._halt_reason = "max_drawdown"
                log.error(
                    f"MAX DRAWDOWN HIT: {dd:.1f}% "
                    f"(peak: ${self._peak_balance:.2f}, "
                    f"current: ${current_balance:.2f}) — HALTING ALL TRADING"
                )

    def periodic_risk_check(self):
        """Run periodic risk checks (call from main loop, not just on trade close)."""
        self._check_drawdown()

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def can_open_trade(self, symbol: str) -> tuple[bool, str]:
        """
        Master gate: check ALL risk conditions before allowing a trade.
        Returns (allowed: bool, reason: str).
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

        # Reduce risk after daily losses
        capital_base = max(self.mt5.account_balance(), 1.0)
        if self._daily_pnl < 0:
            daily_limit = capital_base * (cfg.DAILY_LOSS_LIMIT_PCT / 100)
            if daily_limit > 0:
                loss_ratio = abs(self._daily_pnl) / daily_limit
                if loss_ratio > 0.5:
                    worst_multiplier = min(worst_multiplier, 0.5)

        # Reduce risk after weekly losses
        if self._weekly_pnl < 0:
            weekly_limit = capital_base * (cfg.WEEKLY_LOSS_LIMIT_PCT / 100)
            if weekly_limit > 0:
                loss_ratio = abs(self._weekly_pnl) / weekly_limit
                if loss_ratio > 0.5:
                    worst_multiplier = min(worst_multiplier, 0.5)

        return max(base * worst_multiplier, 0.25)  # minimum 0.25%

    # =====================================================================
    #  TRADE JOURNAL
    # =====================================================================

    def log_trade(self, trade_data: dict):
        """Append a trade entry to the JSON journal."""
        journal = []
        if self._journal_path.exists():
            try:
                with open(self._journal_path, "r") as f:
                    journal = json.load(f)
            except (json.JSONDecodeError, IOError):
                journal = []

        trade_data["timestamp"] = datetime.now(timezone.utc).isoformat()
        journal.append(trade_data)

        with open(self._journal_path, "w") as f:
            json.dump(journal, f, indent=2, default=str)

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
            "peak_balance": self._peak_balance,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
        }
