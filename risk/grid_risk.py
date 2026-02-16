"""
Risk controls for grid trading (inventory, leverage, drawdown).

Pre-production fixes:
- C3: Added _permanent_halt flag — max_drawdown can't be bypassed by halt reason overwrite.
- C4: manual_reset() now resets _peak_equity to current equity.
- H9: clear_errors() now clears the halt if reason was consecutive_errors, and persists.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

from brokers.mt5 import MT5Broker
from config import settings as cfg
from utils.logger import get_logger

log = get_logger("grid_risk")


@dataclass
class RiskStatus:
    halted: bool
    reason: str
    risk_multiplier: float
    warnings: list[str] = field(default_factory=list)


class GridRiskManager:
    """
    Enforces inventory, leverage, drawdown, and loss-limit controls.
    """

    MARGIN_HALT_LEVEL = 200.0
    MARGIN_CAUTION_LEVEL = 400.0

    def __init__(self, broker: MT5Broker):
        self.broker = broker
        self._daily_start = self._day_start_time()
        self._week_start = self._week_start_time()
        self._day_start_equity = 0.0
        self._week_start_equity = 0.0
        self._peak_equity = 0.0
        self._halted = False
        self._halt_reason = ""
        self._permanent_halt = False  # C3: survives halt reason overwrites
        self._risk_multiplier = 1.0
        self._consecutive_errors = 0
        self._last_persisted_hash = ""
        self._restore_state()

    # ── Time helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _day_start_time() -> datetime:
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    @staticmethod
    def _week_start_time() -> datetime:
        day_start = GridRiskManager._day_start_time()
        return day_start - timedelta(days=day_start.weekday())

    # ── State persistence (only when changed) ─────────────────────────────

    def _state_dict(self) -> dict:
        return {
            "daily_start": self._daily_start.isoformat(),
            "weekly_start": self._week_start.isoformat(),
            "day_start_equity": self._day_start_equity,
            "week_start_equity": self._week_start_equity,
            "peak_equity": self._peak_equity,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "permanent_halt": self._permanent_halt,
            "risk_multiplier": self._risk_multiplier,
            "consecutive_errors": self._consecutive_errors,
        }

    def _state_hash(self) -> str:
        d = self._state_dict()
        return str({
            k: round(v, 6) if isinstance(v, float) else v
            for k, v in d.items()
        })

    def _persist_state(self, force: bool = False):
        current_hash = self._state_hash()
        if not force and current_hash == self._last_persisted_hash:
            return
        path = cfg.RISK_STATE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.warning(f"Risk state disabled (cannot create dir): {exc}")
            return
        data = json.dumps(self._state_dict(), indent=2)

        # Strategy 1: Atomic write
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with open(fd, "w", encoding="utf-8") as f:
                f.write(data)
            Path(tmp).replace(path)
            self._last_persisted_hash = current_hash
            return
        except PermissionError:
            pass
        except Exception as exc:
            log.warning(f"Atomic risk save failed: {exc}")

        # Strategy 2: Direct overwrite
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            self._last_persisted_hash = current_hash
            return
        except Exception as exc:
            log.warning(f"Direct risk save failed: {exc}")

        # Strategy 3: Backup file
        import time as _time
        backup = path.with_suffix(f".{int(_time.time())}.bak")
        try:
            with open(backup, "w", encoding="utf-8") as f:
                f.write(data)
            self._last_persisted_hash = current_hash
            log.warning(f"Risk state saved to backup: {backup}")
        except Exception as exc:
            log.error(f"ALL risk save strategies failed: {exc}")

    def _restore_state(self):
        path = cfg.RISK_STATE_PATH
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._daily_start = datetime.fromisoformat(state.get("daily_start"))
            self._week_start = datetime.fromisoformat(state.get("weekly_start"))
            self._day_start_equity = float(state.get("day_start_equity", 0.0))
            self._week_start_equity = float(state.get("week_start_equity", 0.0))
            self._peak_equity = float(state.get("peak_equity", 0.0))
            self._halted = bool(state.get("halted", False))
            self._halt_reason = str(state.get("halt_reason", ""))
            self._permanent_halt = bool(state.get("permanent_halt", False))
            self._risk_multiplier = float(state.get("risk_multiplier", 1.0))
            self._consecutive_errors = int(state.get("consecutive_errors", 0))
            self._last_persisted_hash = self._state_hash()
        except Exception as exc:
            log.warning(f"Failed to restore risk state: {exc}")

    def _account_notional(self) -> float:
        """Compute total notional exposure across all GRID positions."""
        total = 0.0
        positions = self.broker.our_positions()
        for pos in positions:
            symbol = pos.get("symbol", "")
            volume = pos.get("volume", 0.0)
            if not symbol or volume <= 0:
                continue
            sym_info = self.broker.symbol_info(symbol) or {}
            contract_size = sym_info.get("trade_contract_size", 0.0)
            if contract_size <= 0:
                continue
            tick = self.broker.symbol_tick(symbol)
            if tick and tick.get("bid") and tick.get("ask"):
                mid = (tick["bid"] + tick["ask"]) / 2.0
            else:
                mid = pos.get("price_open", 0.0)
            if mid <= 0:
                continue
            total += abs(volume) * contract_size * mid
        return total

    # ── Equity tracking ───────────────────────────────────────────────────

    def update_equity(self, equity: float):
        if equity <= 0:
            return  # reject garbage equity values
        if self._day_start_equity <= 0:
            self._day_start_equity = equity
        if self._week_start_equity <= 0:
            self._week_start_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

    def reset_daily_if_needed(self):
        now = datetime.now(timezone.utc)
        if now.date() != self._daily_start.date():
            self._daily_start = self._day_start_time()
            self._day_start_equity = self.broker.account_equity()
            if self._halt_reason == "daily_loss_limit" and not self._permanent_halt:
                self._halted = False
                self._halt_reason = ""
                log.info("Daily loss halt cleared (new trading day)")

    def reset_weekly_if_needed(self):
        now = datetime.now(timezone.utc)
        week_start = self._week_start_time()
        if week_start.date() != self._week_start.date():
            self._week_start = week_start
            self._week_start_equity = self.broker.account_equity()
            if self._halt_reason == "weekly_loss_limit" and not self._permanent_halt:
                self._halted = False
                self._halt_reason = ""
                log.info("Weekly loss halt cleared (new trading week)")

    # ── Error tracking (for safe mode) ────────────────────────────────────

    def record_error(self):
        """Call when a trading loop error occurs."""
        self._consecutive_errors += 1
        if self._consecutive_errors >= 5:
            self._halted = True
            self._halt_reason = "consecutive_errors"
            log.error(
                f"Safe mode: {self._consecutive_errors} consecutive errors - halting"
            )
        self._persist_state()

    def clear_errors(self):
        """Call on a successful loop iteration.

        H9: Also clears the halt if it was caused by consecutive_errors,
        and persists the change.
        """
        changed = False
        if self._consecutive_errors > 0:
            self._consecutive_errors = 0
            changed = True
        if self._halted and self._halt_reason == "consecutive_errors":
            self._halted = False
            self._halt_reason = ""
            log.info("Consecutive errors halt cleared (successful cycle)")
            changed = True
        if changed:
            self._persist_state()

    # ── Main limit check ──────────────────────────────────────────────────

    def check_limits(
        self,
        symbol: str,
        inventory_lots: float,
        notional: float,
        price: float,
    ) -> RiskStatus:
        acc = self.broker.account_info()
        equity = acc.get("equity", 0.0)
        self.update_equity(equity)
        self.reset_daily_if_needed()
        self.reset_weekly_if_needed()

        # C3: If permanent halt is set, stay halted no matter what
        if self._permanent_halt:
            self._persist_state()
            return RiskStatus(
                halted=True,
                reason=self._halt_reason or "max_drawdown",
                risk_multiplier=0.0,
                warnings=["PERMANENT HALT — requires manual_reset()"],
            )

        halt_reasons: list[str] = []
        warnings: list[str] = []
        risk_mult = 1.0

        # ── Guard against missing account data ───────────────────────
        balance = acc.get("balance", 0.0)
        if equity <= 0 or balance <= 0:
            halt_reasons.append("account_data_missing")
            self._halted = True
            self._halt_reason = "account_data_missing"
            self._risk_multiplier = 0.0
            self._persist_state()
            return RiskStatus(
                halted=True,
                reason="account_data_missing",
                risk_multiplier=0.0,
                warnings=["ACCOUNT DATA MISSING — equity/balance <= 0"],
            )

        account_notional = self._account_notional()
        if account_notional > 0 and acc.get("margin_level", 0.0) <= 0:
            halt_reasons.append("margin_data_missing")

        # ── Drawdown (catastrophic — sets permanent halt) ────────────
        if self._peak_equity > 0:
            dd_pct = (self._peak_equity - equity) / self._peak_equity * 100
            if dd_pct >= cfg.MAX_DRAWDOWN_PCT:
                halt_reasons.append("max_drawdown")
            elif dd_pct >= cfg.MAX_DRAWDOWN_PCT * 0.7:
                warnings.append(f"drawdown_warning({dd_pct:.1f}%)")
                risk_mult = min(risk_mult, 0.5)

        # ── Daily loss ───────────────────────────────────────────────
        if self._day_start_equity > 0:
            daily_pct = (equity - self._day_start_equity) / self._day_start_equity * 100
            if daily_pct <= -cfg.DAILY_LOSS_LIMIT_PCT:
                halt_reasons.append("daily_loss_limit")
            elif daily_pct <= -cfg.DAILY_LOSS_LIMIT_PCT * 0.7:
                warnings.append(f"daily_loss_warning({daily_pct:.1f}%)")
                risk_mult = min(risk_mult, 0.5)

        # ── Weekly loss ──────────────────────────────────────────────
        if self._week_start_equity > 0:
            weekly_pct = (equity - self._week_start_equity) / self._week_start_equity * 100
            if weekly_pct <= -cfg.WEEKLY_LOSS_LIMIT_PCT:
                halt_reasons.append("weekly_loss_limit")
            elif weekly_pct <= -cfg.WEEKLY_LOSS_LIMIT_PCT * 0.7:
                risk_mult = min(risk_mult, 0.5)

        # ── Inventory ─────────────────────────────────────────────────
        if abs(inventory_lots) > cfg.MAX_INVENTORY_LOTS:
            halt_reasons.append("inventory_limit")

        # ── Leverage / notional ───────────────────────────────────────
        if equity > 0:
            leverage = account_notional / equity
            if leverage > cfg.MAX_LEVERAGE:
                halt_reasons.append("leverage_limit")
            elif leverage > cfg.MAX_LEVERAGE * 0.8:
                warnings.append(f"leverage_warning({leverage:.1f}x)")
                risk_mult = min(risk_mult, 0.5)

            if account_notional > equity * cfg.MAX_NOTIONAL_MULT_EQUITY:
                halt_reasons.append("notional_limit")

            # Per-symbol notional cap (still enforced)
            if notional > equity * cfg.MAX_NOTIONAL_MULT_EQUITY:
                halt_reasons.append("symbol_notional_limit")

        # ── Margin level ──────────────────────────────────────────────
        margin_level = acc.get("margin_level", 0.0)
        if margin_level > 0:
            if margin_level < self.MARGIN_HALT_LEVEL:
                halt_reasons.append("margin_level")
            elif margin_level < self.MARGIN_CAUTION_LEVEL:
                warnings.append(f"margin_caution({margin_level:.0f}%)")
                risk_mult = min(risk_mult, 0.5)

        # ── Consecutive errors ────────────────────────────────────────
        if self._consecutive_errors >= 5:
            halt_reasons.append("consecutive_errors")

        # ── Resolve halt state ────────────────────────────────────────
        if halt_reasons:
            reason = halt_reasons[0]
            if not self._halted or self._halt_reason != reason:
                log.warning(
                    f"RISK HALT: {reason} (all breaches: {halt_reasons})"
                )
            self._halted = True
            self._halt_reason = reason
            # C3: max_drawdown sets permanent halt — cannot be overwritten
            if "max_drawdown" in halt_reasons:
                self._permanent_halt = True
                log.error("PERMANENT HALT: max_drawdown — requires manual_reset()")
        else:
            # No breaches — clear halt (permanent halt checked at top)
            if self._halted:
                log.info(
                    f"Risk halt cleared: {self._halt_reason} condition resolved"
                )
                self._halted = False
                self._halt_reason = ""

        self._risk_multiplier = risk_mult
        self._persist_state()

        return RiskStatus(
            halted=self._halted,
            reason=self._halt_reason,
            risk_multiplier=risk_mult,
            warnings=warnings,
        )

    def manual_reset(self):
        """Operator-initiated reset (e.g., after fixing max_drawdown cause).

        C4: Also resets _peak_equity to current equity so the drawdown
        check doesn't immediately re-trigger.
        """
        equity = self.broker.account_equity()
        log.info(
            f"Manual risk reset (was: halted={self._halted} "
            f"reason={self._halt_reason} peak={self._peak_equity:.2f} "
            f"current_equity={equity:.2f})"
        )
        self._halted = False
        self._halt_reason = ""
        self._permanent_halt = False
        self._consecutive_errors = 0
        # C4: Reset peak to current equity so DD check starts fresh
        if equity > 0:
            self._peak_equity = equity
        self._persist_state(force=True)
