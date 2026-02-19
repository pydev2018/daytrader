from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from brokers.mt5 import MT5Broker
from config import settings as cfg
from utils.logger import get_logger

log = get_logger("risk_manager")


@dataclass
class RiskStatus:
    halted: bool
    reason: str
    risk_multiplier: float
    warnings: list[str] = field(default_factory=list)


@dataclass
class RiskCycleContext:
    account_info: dict
    account_notional: float


class RiskManager:
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
        self._permanent_halt = False
        self._risk_multiplier = 1.0
        self._consecutive_errors = 0
        self._last_persisted_hash = ""
        self._restore_state()

    @staticmethod
    def _day_start_time() -> datetime:
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    @staticmethod
    def _week_start_time() -> datetime:
        day_start = RiskManager._day_start_time()
        return day_start - timedelta(days=day_start.weekday())

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

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            self._last_persisted_hash = current_hash
        except Exception as exc:
            log.warning(f"Direct risk save failed: {exc}")

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

    def update_equity(self, equity: float):
        if equity <= 0:
            return
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

    def reset_weekly_if_needed(self):
        week_start = self._week_start_time()
        if week_start.date() != self._week_start.date():
            self._week_start = week_start
            self._week_start_equity = self.broker.account_equity()
            if self._halt_reason == "weekly_loss_limit" and not self._permanent_halt:
                self._halted = False
                self._halt_reason = ""

    def record_error(self):
        self._consecutive_errors += 1
        if self._consecutive_errors >= 5:
            self._halted = True
            self._halt_reason = "consecutive_errors"
        self._persist_state()

    def record_success(self):
        changed = False
        if self._consecutive_errors > 0:
            self._consecutive_errors = 0
            changed = True
        if self._halted and self._halt_reason == "consecutive_errors":
            self._halted = False
            self._halt_reason = ""
            changed = True
        if changed:
            self._persist_state()

    def check_limits(
        self,
        symbol: str,
        inventory_lots: float,
        notional: float,
        price: float,
        cycle: RiskCycleContext | None = None,
    ) -> RiskStatus:
        acc = cycle.account_info if cycle else self.broker.account_info()
        equity = acc.get("equity", 0.0)
        balance = acc.get("balance", 0.0)
        self.update_equity(equity)
        self.reset_daily_if_needed()
        self.reset_weekly_if_needed()

        if self._permanent_halt:
            return RiskStatus(
                halted=True,
                reason=self._halt_reason or "max_drawdown",
                risk_multiplier=0.0,
                warnings=["PERMANENT HALT"],
            )

        halt_reasons: list[str] = []
        warnings: list[str] = []
        risk_mult = 1.0

        if equity <= 0 or balance <= 0:
            halt_reasons.append("account_data_missing")

        account_notional = cycle.account_notional if cycle else self._account_notional()
        if abs(inventory_lots) > cfg.MAX_INVENTORY_LOTS:
            halt_reasons.append("inventory_limit")

        if self._peak_equity > 0 and equity > 0:
            dd_pct = (self._peak_equity - equity) / self._peak_equity * 100
            if dd_pct >= cfg.MAX_DRAWDOWN_PCT:
                halt_reasons.append("max_drawdown")
            elif dd_pct >= cfg.MAX_DRAWDOWN_PCT * 0.7:
                warnings.append(f"drawdown_warning({dd_pct:.1f}%)")
                risk_mult = min(risk_mult, 0.5)

        if self._day_start_equity > 0:
            daily_pct = (equity - self._day_start_equity) / self._day_start_equity * 100
            if daily_pct <= -cfg.DAILY_LOSS_LIMIT_PCT:
                halt_reasons.append("daily_loss_limit")
            elif daily_pct <= -cfg.DAILY_LOSS_LIMIT_PCT * 0.7:
                warnings.append(f"daily_loss_warning({daily_pct:.1f}%)")
                risk_mult = min(risk_mult, 0.5)

        if self._week_start_equity > 0:
            weekly_pct = (equity - self._week_start_equity) / self._week_start_equity * 100
            if weekly_pct <= -cfg.WEEKLY_LOSS_LIMIT_PCT:
                halt_reasons.append("weekly_loss_limit")
            elif weekly_pct <= -cfg.WEEKLY_LOSS_LIMIT_PCT * 0.7:
                risk_mult = min(risk_mult, 0.5)

        if equity > 0:
            leverage = account_notional / equity if account_notional > 0 else 0.0
            if leverage > cfg.MAX_LEVERAGE:
                halt_reasons.append("leverage_limit")
            elif leverage > cfg.MAX_LEVERAGE * 0.8:
                warnings.append(f"leverage_warning({leverage:.1f}x)")
                risk_mult = min(risk_mult, 0.5)
            if account_notional > equity * cfg.MAX_NOTIONAL_MULT_EQUITY:
                halt_reasons.append("notional_limit")
            if notional > equity * cfg.MAX_NOTIONAL_MULT_EQUITY:
                halt_reasons.append("symbol_notional_limit")

        margin_level = acc.get("margin_level", 0.0)
        if margin_level > 0:
            if margin_level < self.MARGIN_HALT_LEVEL:
                halt_reasons.append("margin_level")
            elif margin_level < self.MARGIN_CAUTION_LEVEL:
                warnings.append(f"margin_caution({margin_level:.0f}%)")
                risk_mult = min(risk_mult, 0.5)

        if self._consecutive_errors >= 5:
            halt_reasons.append("consecutive_errors")

        if halt_reasons:
            self._halted = True
            self._halt_reason = halt_reasons[0]
            if "max_drawdown" in halt_reasons:
                self._permanent_halt = True
        else:
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

    def begin_cycle(self) -> RiskCycleContext:
        return RiskCycleContext(
            account_info=self.broker.account_info(),
            account_notional=self._account_notional(),
        )

