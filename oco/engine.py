from __future__ import annotations

import signal
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

from alerts.telegram import TelegramAlerter
from brokers.mt5 import MT5Broker
from config import settings as cfg
from oco.order_manager import OcoOrderManager
from risk.manager import RiskManager
from utils.logger import get_logger

log = get_logger("oco_engine")

_shutdown_requested = False

FRIDAY_CLOSE_HOUR = 21
FRIDAY_CLOSE_MINUTE = 50
SUNDAY_OPEN_HOUR = 22
SUNDAY_OPEN_WARMUP_MINUTES = 15


@dataclass
class PendingSpec:
    symbol: str
    side: str
    price: float
    volume: float
    comment: str
    rung_id: str = "OCO"
    state: str = "ENTRY"
    order_type: str = "STOP"


@dataclass
class OcoSymbolState:
    armed_at: float = 0.0
    arm_expires_at: float = 0.0
    cooldown_until: float = 0.0
    arm_id: str = ""
    buy_ticket: int = 0
    sell_ticket: int = 0
    position_ticket: int = 0
    position_side: str = ""
    entry_price: float = 0.0
    sl_distance: float = 0.0
    best_price: float = 0.0
    entry_ts: float = 0.0
    setup_cached_at: float = 0.0
    setup_cached_spread: float = 0.0
    setup_cached: BreakoutSetup | None = None


@dataclass
class BreakoutSetup:
    buy_stop: float
    sell_stop: float
    sl_distance: float


@dataclass
class SymbolTelemetry:
    cycles: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    idle: int = 0
    armed: int = 0
    position: int = 0
    cooldown: int = 0
    halted: int = 0
    errors: int = 0
    arms: int = 0
    time_stops: int = 0
    flatten_events: int = 0


def _signal_handler(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    log.info("Shutdown signal received - finishing current cycle ...")


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _is_market_open() -> bool:
    now = datetime.now(timezone.utc)
    weekday = now.weekday()

    if weekday == 5:
        return False

    if weekday == 6:
        open_minute = SUNDAY_OPEN_HOUR * 60 + SUNDAY_OPEN_WARMUP_MINUTES
        current_minute = now.hour * 60 + now.minute
        return current_minute >= open_minute

    if weekday == 4:
        close_minute = FRIDAY_CLOSE_HOUR * 60 + FRIDAY_CLOSE_MINUTE
        current_minute = now.hour * 60 + now.minute
        return current_minute < close_minute

    return True


def _is_oco_comment(comment: str) -> bool:
    return (comment or "").lower().startswith("oco:")


def _position_side(position_type: int) -> str:
    return "BUY" if position_type == mt5.POSITION_TYPE_BUY else "SELL"


def _compute_breakout_setup(
    rates: pd.DataFrame,
    spread: float,
    point: float,
) -> BreakoutSetup | None:
    if rates is None or rates.empty or len(rates) < cfg.OCO_BREAKOUT_LOOKBACK:
        return None

    window = rates.tail(cfg.OCO_BREAKOUT_LOOKBACK)
    if len(window) < 5:
        return None

    highs = window["high"]
    lows = window["low"]
    if highs.empty or lows.empty:
        return None

    breakout_high = float(highs.max())
    breakout_low = float(lows.min())

    tr = highs - lows
    atr = float(tr.tail(min(14, len(tr))).mean()) if len(tr) else 0.0
    atr = max(atr, point * 10)

    buffer = max(
        cfg.OCO_BUFFER_ATR_MULT * atr,
        cfg.OCO_BUFFER_SPREAD_MULT * spread,
        point * 2,
    )

    buy_stop = breakout_high + buffer
    sell_stop = breakout_low - buffer

    if buy_stop <= sell_stop:
        return None

    sl_distance = max(cfg.OCO_SL_SPACING_MULT * atr, point * 10)
    return BreakoutSetup(
        buy_stop=buy_stop,
        sell_stop=sell_stop,
        sl_distance=sl_distance,
    )


class OcoEngine:
    def __init__(self):
        self.broker = MT5Broker()
        self.order_mgr = OcoOrderManager(self.broker)
        self.risk_mgr = RiskManager(self.broker)
        self.alerter = TelegramAlerter(
            bot_token=cfg.TELEGRAM_BOT_TOKEN,
            chat_id=cfg.TELEGRAM_CHAT_ID,
        )
        self.symbol_states: dict[str, OcoSymbolState] = {}
        self.symbols: list[str] = list(cfg.OCO_SYMBOLS)
        self._last_status_log_ts: float = 0.0
        self._status_log_interval: float = 300.0
        self._setup_refresh_seconds: float = max(1.0, cfg.OCO_SETUP_REFRESH_SECONDS)
        self._smart_setup_refresh_enabled: bool = bool(cfg.OCO_SMART_SETUP_REFRESH_ENABLED)
        self._setup_spread_shock_ratio: float = max(1.0, cfg.OCO_SETUP_SPREAD_SHOCK_RATIO)
        self._market_closed_cleanup_done: bool = False
        self._telemetry_last_log_ts: float = 0.0
        self._telemetry_interval: float = max(10.0, cfg.OCO_TELEMETRY_INTERVAL_SECONDS)
        self._loop_count: int = 0
        self._loop_total_ms: float = 0.0
        self._loop_max_ms: float = 0.0
        self._adaptive_loop_enabled: bool = bool(cfg.OCO_ADAPTIVE_LOOP_ENABLED)
        self._loop_min_seconds: float = max(0.1, cfg.OCO_LOOP_MIN_SECONDS)
        self._loop_max_seconds: float = max(self._loop_min_seconds, cfg.OCO_LOOP_MAX_SECONDS)
        self._current_loop_sleep: float = max(cfg.OCO_LOOP_SECONDS, self._loop_min_seconds)
        self._broker_calls: dict[str, int] = defaultdict(int)
        self._order_calls: dict[str, int] = defaultdict(int)
        self._symbol_telemetry: dict[str, SymbolTelemetry] = {
            symbol: SymbolTelemetry() for symbol in self.symbols
        }

    def start(self):
        log.info("=" * 70)
        log.info("  OCO BREAKOUT SYSTEM - Starting")
        log.info("=" * 70)

        if not self._bcall("connect", self.broker.connect):
            log.error("Failed to connect to MT5 - exiting")
            return

        acc = self._bcall("account_info", self.broker.account_info)
        log.info(
            f"Account: {acc.get('login')} | "
            f"Balance: ${acc.get('balance', 0):,.2f} | "
            f"Equity: ${acc.get('equity', 0):,.2f} | "
            f"Leverage: 1:{acc.get('leverage', 0)}"
        )

        model = self._bcall("account_model", self.broker.account_model)
        if not model.get("is_hedging"):
            log.error("Hedging account required — refusing to start on netting/FIFO")
            self.broker.disconnect()
            return

        if not self.symbols:
            log.error("No symbols configured in OCO_SYMBOLS - exiting")
            self.broker.disconnect()
            return

        for symbol in self.symbols:
            self.symbol_states[symbol] = OcoSymbolState()
            if not self._bcall("select_symbol", self.broker.select_symbol, symbol):
                log.warning(f"{symbol}: symbol not available in MarketWatch")

        self._cancel_all_pending_orders()
        self._adopt_open_positions()

        self.alerter.bot_status("STARTED", f"OCO symbols: {', '.join(self.symbols)}")

        try:
            self._main_loop()
        finally:
            self._bcall("disconnect", self.broker.disconnect)
            self.alerter.bot_status("STOPPED", "OCO engine stopped")

    def _main_loop(self):
        while not _shutdown_requested:
            cycle_start = time.perf_counter()
            if not _is_market_open():
                if not self._market_closed_cleanup_done:
                    self._cancel_all_pending_orders()
                    self._market_closed_cleanup_done = True
                time.sleep(max(cfg.OCO_LOOP_SECONDS, 1.0))
                continue

            self._market_closed_cleanup_done = False
            cycle = self.risk_mgr.begin_cycle()
            decisions: list[str] = []

            for symbol in self.symbols:
                try:
                    decision = self._process_symbol(symbol, cycle)
                    decisions.append(decision)
                    self.risk_mgr.record_success()
                except Exception as exc:
                    self._symbol_telemetry[symbol].errors += 1
                    self.risk_mgr.record_error()
                    log.exception(f"{symbol}: loop error: {exc}")
                    decisions.append("ERROR")

            cycle_ms = (time.perf_counter() - cycle_start) * 1000.0
            self._loop_count += 1
            self._loop_total_ms += cycle_ms
            self._loop_max_ms = max(self._loop_max_ms, cycle_ms)
            self._log_status()
            self._log_telemetry()
            sleep_seconds = self._compute_loop_sleep(cycle_ms, decisions)
            self._current_loop_sleep = sleep_seconds
            time.sleep(sleep_seconds)

    def _process_symbol(self, symbol: str, cycle):
        symbol_start = time.perf_counter()
        state = self.symbol_states[symbol]
        tick = self._bcall("symbol_tick", self.broker.symbol_tick, symbol)
        if tick is None:
            return "IDLE"

        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", 0.0))
        if bid <= 0 or ask <= 0:
            return "IDLE"

        mid = (bid + ask) * 0.5
        spread = max(ask - bid, 0.0)

        positions = self._bcall("our_positions", self.broker.our_positions, symbol)
        pending = self._bcall("pending_orders", self.broker.pending_orders, symbol)

        inventory_lots = sum(
            (1.0 if p.get("type") == mt5.POSITION_TYPE_BUY else -1.0) * float(p.get("volume", 0.0))
            for p in positions
        )
        sym_info = self._bcall("symbol_info", self.broker.symbol_info, symbol) or {}
        point = float(sym_info.get("point", 0.00001))
        contract_size = float(sym_info.get("trade_contract_size", 100000.0))
        notional = abs(inventory_lots) * mid * contract_size

        risk = self.risk_mgr.check_limits(
            symbol=symbol,
            inventory_lots=inventory_lots,
            notional=notional,
            price=mid,
            cycle=cycle,
        )

        if risk.halted:
            self._flatten_symbol(symbol, pending, inventory_lots, reason=risk.reason)
            self._record_symbol_timing(symbol, symbol_start, "HALTED")
            return "HALTED"

        oco_positions = [p for p in positions if _is_oco_comment(p.get("comment", ""))]
        if len(oco_positions) > 1:
            log.warning(f"{symbol}: multiple OCO positions detected; flattening")
            self._flatten_symbol(symbol, pending, inventory_lots, reason="multiple_positions")
            self._record_symbol_timing(symbol, symbol_start, "HALTED")
            return "HALTED"

        if oco_positions:
            self._manage_open_position(symbol, state, oco_positions[0], tick, pending, point)
            self._record_symbol_timing(symbol, symbol_start, "POSITION")
            return "POSITION"

        if state.position_ticket:
            state.cooldown_until = max(
                state.cooldown_until,
                time.time() + cfg.OCO_REENTRY_COOLDOWN_SECONDS,
            )
            self._cancel_oco_pending(symbol, pending)

        state.position_ticket = 0
        state.position_side = ""
        state.entry_price = 0.0
        state.best_price = 0.0
        state.entry_ts = 0.0

        self._ensure_oco_orders(
            symbol,
            state,
            tick,
            spread,
            point,
            risk.risk_multiplier,
            pending,
        )

        now = time.time()
        if state.buy_ticket and state.sell_ticket:
            decision = "ARMED"
        elif now < state.cooldown_until:
            decision = "COOLDOWN"
        else:
            decision = "IDLE"
        self._record_symbol_timing(symbol, symbol_start, decision)
        return decision

    def _ensure_oco_orders(
        self,
        symbol: str,
        state: OcoSymbolState,
        tick: dict,
        spread: float,
        point: float,
        risk_multiplier: float,
        pending: list[dict],
    ):
        now = time.time()
        if now < state.cooldown_until:
            self._cancel_oco_pending(symbol, pending)
            return

        pending_by_ticket = {int(p.get("ticket", 0)): p for p in pending}
        have_buy = state.buy_ticket and state.buy_ticket in pending_by_ticket
        have_sell = state.sell_ticket and state.sell_ticket in pending_by_ticket

        if state.buy_ticket and state.sell_ticket and have_buy and have_sell and now < state.arm_expires_at:
            return

        if (have_buy and not have_sell) or (have_sell and not have_buy):
            self._cancel_oco_pending(symbol, pending)
            state.buy_ticket = 0
            state.sell_ticket = 0
            state.armed_at = 0.0
            state.arm_expires_at = 0.0
            return

        if now >= state.arm_expires_at and (state.buy_ticket or state.sell_ticket):
            self._cancel_oco_pending(symbol, pending)
            state.buy_ticket = 0
            state.sell_ticket = 0
            state.armed_at = 0.0
            state.arm_expires_at = 0.0

        setup = self._get_cached_setup(symbol, state, spread, point)
        if setup is None:
            return

        base_volume = cfg.BASE_ORDER_SIZE_LOTS * cfg.OCO_SIZE_MULT * max(risk_multiplier, 0.0)
        volume = self.broker.normalize_volume(symbol, base_volume)
        if volume <= 0:
            return

        mid = (float(tick["ask"]) + float(tick["bid"])) * 0.5
        arm_id = str(int(now))

        buy_spec = PendingSpec(
            symbol=symbol,
            side="BUY",
            price=self.broker.normalize_price(symbol, setup.buy_stop),
            volume=volume,
            comment=f"oco:{symbol}:B:{arm_id}",
        )
        sell_spec = PendingSpec(
            symbol=symbol,
            side="SELL",
            price=self.broker.normalize_price(symbol, setup.sell_stop),
            volume=volume,
            comment=f"oco:{symbol}:S:{arm_id}",
        )

        buy_res = self._ocall("place_stop", self.order_mgr.place_stop, buy_spec, current_price=mid)
        sell_res = self._ocall("place_stop", self.order_mgr.place_stop, sell_spec, current_price=mid)

        buy_ok = bool(buy_res and buy_res.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010))
        sell_ok = bool(sell_res and sell_res.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010))

        if not (buy_ok and sell_ok):
            self._cancel_oco_pending(symbol, self._bcall("pending_orders", self.broker.pending_orders, symbol))
            state.buy_ticket = 0
            state.sell_ticket = 0
            state.armed_at = 0.0
            state.arm_expires_at = 0.0
            return

        state.arm_id = arm_id
        state.buy_ticket = int(buy_res.get("order", 0))
        state.sell_ticket = int(sell_res.get("order", 0))
        state.armed_at = now
        state.arm_expires_at = now + cfg.OCO_ARM_TTL_SECONDS
        state.sl_distance = setup.sl_distance
        self._symbol_telemetry[symbol].arms += 1
        log.info(
            f"{symbol}: armed OCO buy_stop={buy_spec.price:.5f} sell_stop={sell_spec.price:.5f} "
            f"vol={volume:.2f} ttl={cfg.OCO_ARM_TTL_SECONDS}s"
        )

    def _manage_open_position(
        self,
        symbol: str,
        state: OcoSymbolState,
        position: dict,
        tick: dict,
        pending: list[dict],
        point: float,
    ):
        self._cancel_oco_pending(symbol, pending)
        state.buy_ticket = 0
        state.sell_ticket = 0
        state.armed_at = 0.0
        state.arm_expires_at = 0.0

        ticket = int(position.get("ticket", 0))
        side = _position_side(position.get("type", mt5.POSITION_TYPE_BUY))
        entry_price = float(position.get("price_open", 0.0))
        volume = float(position.get("volume", 0.0))
        pos_sl = float(position.get("sl", 0.0))

        now = time.time()
        if state.position_ticket != ticket:
            state.position_ticket = ticket
            state.position_side = side
            state.entry_price = entry_price
            state.entry_ts = now
            if side == "BUY":
                state.best_price = float(tick.get("bid", entry_price))
            else:
                state.best_price = float(tick.get("ask", entry_price))
            if state.sl_distance <= 0:
                state.sl_distance = max(abs(entry_price - pos_sl), point * 20)

        if state.sl_distance <= 0:
            state.sl_distance = point * 20

        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", 0.0))

        if side == "BUY":
            current_price = bid
            state.best_price = max(state.best_price, current_price)
            gain = current_price - state.entry_price
        else:
            current_price = ask
            if state.best_price <= 0:
                state.best_price = current_price
            state.best_price = min(state.best_price, current_price)
            gain = state.entry_price - current_price

        if pos_sl <= 0:
            if side == "BUY":
                new_sl = state.entry_price - state.sl_distance
            else:
                new_sl = state.entry_price + state.sl_distance
            self._bcall("modify_position_tp", self.broker.modify_position_tp, ticket, symbol, sl=new_sl, tp=0.0)

        min_distance = self._bcall("get_min_distance", self.broker.get_min_distance, symbol)
        extra_distance = max(cfg.OCO_SL_MIN_DISTANCE_BUFFER_POINTS, 0.0) * point
        min_distance = max(float(min_distance or 0.0) + extra_distance, point)

        be_trigger_gain = state.sl_distance * cfg.OCO_BREAK_EVEN_ACTIVATE_R
        be_offset = cfg.OCO_BREAK_EVEN_OFFSET_POINTS * point
        be_done = False

        if gain >= be_trigger_gain:
            if side == "BUY":
                be_target = self.broker.normalize_price(symbol, state.entry_price + be_offset)
                max_valid_sl = self.broker.normalize_price(symbol, current_price - min_distance)
                be_sl = min(be_target, max_valid_sl)
                should_update_be = be_sl > pos_sl and be_sl < current_price
            else:
                be_target = self.broker.normalize_price(symbol, state.entry_price - be_offset)
                min_valid_sl = self.broker.normalize_price(symbol, current_price + min_distance)
                be_sl = max(be_target, min_valid_sl)
                should_update_be = (pos_sl <= 0 or be_sl < pos_sl) and be_sl > current_price

            if should_update_be:
                be_result = self._bcall(
                    "modify_position_tp",
                    self.broker.modify_position_tp,
                    ticket,
                    symbol,
                    sl=be_sl,
                    tp=0.0,
                )
                if be_result and be_result.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010, 10025):
                    pos_sl = be_sl

        if side == "BUY":
            be_floor = state.entry_price + be_offset - point
            be_done = pos_sl > 0 and pos_sl >= be_floor
        else:
            be_ceiling = state.entry_price - be_offset + point
            be_done = pos_sl > 0 and pos_sl <= be_ceiling

        if be_done and gain >= state.sl_distance * cfg.OCO_TRAIL_ACTIVATE_R:
            trail_gap = state.sl_distance * cfg.OCO_TRAIL_SPACING_MULT
            if side == "BUY":
                raw_sl = self.broker.normalize_price(symbol, state.best_price - trail_gap)
                max_valid_sl = self.broker.normalize_price(symbol, current_price - min_distance)
                new_sl = min(raw_sl, max_valid_sl)
                should_update = new_sl > pos_sl and new_sl < current_price
            else:
                raw_sl = self.broker.normalize_price(symbol, state.best_price + trail_gap)
                min_valid_sl = self.broker.normalize_price(symbol, current_price + min_distance)
                new_sl = max(raw_sl, min_valid_sl)
                should_update = (pos_sl <= 0 or new_sl < pos_sl) and new_sl > current_price
            if should_update:
                self._bcall("modify_position_tp", self.broker.modify_position_tp, ticket, symbol, sl=new_sl, tp=0.0)

        if now - state.entry_ts >= cfg.OCO_TIME_STOP_SECONDS:
            close_side = "SELL" if side == "BUY" else "BUY"
            closed = self._ocall(
                "close_position_by_ticket",
                self.order_mgr.close_position_by_ticket,
                symbol=symbol,
                position_ticket=ticket,
                volume=volume,
                side=close_side,
                reason="oco_time_stop",
            )
            if closed:
                self._symbol_telemetry[symbol].time_stops += 1
                state.cooldown_until = now + cfg.OCO_REENTRY_COOLDOWN_SECONDS
                state.position_ticket = 0
                state.position_side = ""
                state.entry_price = 0.0
                state.best_price = 0.0
                state.entry_ts = 0.0
                state.buy_ticket = 0
                state.sell_ticket = 0
                state.armed_at = 0.0
                state.arm_expires_at = 0.0

    def _get_cached_setup(
        self,
        symbol: str,
        state: OcoSymbolState,
        spread: float,
        point: float,
    ) -> BreakoutSetup | None:
        now = time.time()
        cached_spread_ref = max(state.setup_cached_spread, point)
        spread_ratio = spread / cached_spread_ref if cached_spread_ref > 0 else 1.0
        refresh_window = self._effective_setup_refresh_seconds(state, spread_ratio)

        if (
            state.setup_cached is not None
            and now - state.setup_cached_at < refresh_window
            and spread_ratio <= self._setup_spread_shock_ratio
        ):
            return state.setup_cached

        rates = self._bcall(
            "get_rates",
            self.broker.get_rates,
            symbol,
            cfg.OCO_TIMEFRAME,
            cfg.OCO_BREAKOUT_LOOKBACK + 5,
        )
        setup = _compute_breakout_setup(rates, spread, point)
        state.setup_cached = setup
        state.setup_cached_at = now
        state.setup_cached_spread = max(spread, point)
        return setup

    def _effective_setup_refresh_seconds(
        self,
        state: OcoSymbolState,
        spread_ratio: float,
    ) -> float:
        refresh = self._setup_refresh_seconds
        if not self._smart_setup_refresh_enabled:
            return refresh

        now = time.time()
        if state.buy_ticket and state.sell_ticket:
            refresh *= 0.6
        elif now < state.cooldown_until:
            refresh *= 2.0
        else:
            refresh *= 1.5

        if spread_ratio >= self._setup_spread_shock_ratio:
            refresh *= 2.0

        return max(1.0, refresh)

    def _compute_loop_sleep(self, cycle_ms: float, decisions: list[str]) -> float:
        base = max(cfg.OCO_LOOP_SECONDS, 0.1)
        if not self._adaptive_loop_enabled:
            return base

        has_position = any(d == "POSITION" for d in decisions)
        has_armed = any(d == "ARMED" for d in decisions)
        has_halted = any(d == "HALTED" for d in decisions)

        target = base
        if has_halted or has_position:
            target = max(self._loop_min_seconds, base * 0.6)
        elif has_armed:
            target = self._loop_min_seconds
        else:
            target = min(self._loop_max_seconds, base * 1.6)

        current_load_ratio = cycle_ms / max(target * 1000.0, 1.0)
        if current_load_ratio > 0.7:
            target = min(self._loop_max_seconds, max(target, cycle_ms / 1000.0 * 1.4))

        smoothed = self._current_loop_sleep * 0.7 + target * 0.3
        return max(self._loop_min_seconds, min(self._loop_max_seconds, smoothed))

    def _flatten_symbol(
        self,
        symbol: str,
        pending: list[dict],
        inventory_lots: float,
        reason: str,
    ):
        self._cancel_all_symbol_pending(symbol, pending)
        if abs(inventory_lots) > 1e-8:
            self._ocall("unwind_position", self.order_mgr.unwind_position, symbol, inventory_lots)

        state = self.symbol_states[symbol]
        state.cooldown_until = time.time() + cfg.OCO_REENTRY_COOLDOWN_SECONDS
        state.buy_ticket = 0
        state.sell_ticket = 0
        state.armed_at = 0.0
        state.arm_expires_at = 0.0
        state.position_ticket = 0
        state.position_side = ""
        state.entry_price = 0.0
        state.best_price = 0.0
        state.entry_ts = 0.0
        self._symbol_telemetry[symbol].flatten_events += 1
        log.warning(f"{symbol}: flattened due to {reason}")

    def _cancel_all_symbol_pending(self, symbol: str, pending: list[dict] | None = None):
        orders = pending if pending is not None else self._bcall("pending_orders", self.broker.pending_orders, symbol)
        for order in orders:
            ticket = int(order.get("ticket", 0))
            if ticket:
                self._ocall("cancel", self.order_mgr.cancel, ticket, reason="oco_cleanup")

    def _cancel_oco_pending(self, symbol: str, pending: list[dict] | None = None):
        orders = pending if pending is not None else self._bcall("pending_orders", self.broker.pending_orders, symbol)
        for order in orders:
            comment = order.get("comment", "")
            if not _is_oco_comment(comment):
                continue
            ticket = int(order.get("ticket", 0))
            if ticket:
                self._ocall("cancel", self.order_mgr.cancel, ticket, reason="oco_cancel_opposite")

    def _cancel_all_pending_orders(self):
        for order in self._bcall("pending_orders", self.broker.pending_orders):
            ticket = int(order.get("ticket", 0))
            if ticket:
                self._ocall("cancel", self.order_mgr.cancel, ticket, reason="startup_cleanup")

    def _adopt_open_positions(self):
        for symbol in self.symbols:
            positions = self._bcall("our_positions", self.broker.our_positions, symbol)
            oco_positions = [p for p in positions if _is_oco_comment(p.get("comment", ""))]
            if not oco_positions:
                continue
            pos = oco_positions[0]
            state = self.symbol_states[symbol]
            state.position_ticket = int(pos.get("ticket", 0))
            state.position_side = _position_side(pos.get("type", mt5.POSITION_TYPE_BUY))
            state.entry_price = float(pos.get("price_open", 0.0))
            state.entry_ts = time.time()
            state.sl_distance = abs(state.entry_price - float(pos.get("sl", 0.0)))
            state.best_price = state.entry_price
            state.buy_ticket = 0
            state.sell_ticket = 0
            state.armed_at = 0.0
            state.arm_expires_at = 0.0
            log.info(f"{symbol}: adopted open OCO position ticket={state.position_ticket}")

    def _record_symbol_timing(self, symbol: str, symbol_start: float, decision: str):
        elapsed_ms = (time.perf_counter() - symbol_start) * 1000.0
        stats = self._symbol_telemetry[symbol]
        stats.cycles += 1
        stats.total_ms += elapsed_ms
        stats.max_ms = max(stats.max_ms, elapsed_ms)
        if decision == "POSITION":
            stats.position += 1
        elif decision == "ARMED":
            stats.armed += 1
        elif decision == "COOLDOWN":
            stats.cooldown += 1
        elif decision == "HALTED":
            stats.halted += 1
        else:
            stats.idle += 1

    def _bcall(self, name: str, func, *args, **kwargs):
        self._broker_calls[name] += 1
        return func(*args, **kwargs)

    def _ocall(self, name: str, func, *args, **kwargs):
        self._order_calls[name] += 1
        return func(*args, **kwargs)

    def _log_telemetry(self):
        now = time.time()
        if now - self._telemetry_last_log_ts < self._telemetry_interval:
            return
        self._telemetry_last_log_ts = now

        avg_loop_ms = self._loop_total_ms / self._loop_count if self._loop_count else 0.0
        broker_summary = ", ".join(
            f"{k}={v}" for k, v in sorted(self._broker_calls.items())
        ) or "none"
        order_summary = ", ".join(
            f"{k}={v}" for k, v in sorted(self._order_calls.items())
        ) or "none"

        log.info(
            "OCO telemetry | "
            f"loops={self._loop_count} avg_loop_ms={avg_loop_ms:.2f} "
            f"max_loop_ms={self._loop_max_ms:.2f} sleep_s={self._current_loop_sleep:.2f} "
            f"broker_calls[{broker_summary}] "
            f"order_calls[{order_summary}]"
        )

        for symbol in self.symbols:
            s = self._symbol_telemetry[symbol]
            avg_ms = s.total_ms / s.cycles if s.cycles else 0.0
            log.info(
                f"OCO telemetry {symbol} | cycles={s.cycles} avg_ms={avg_ms:.2f} "
                f"max_ms={s.max_ms:.2f} state[idle={s.idle} armed={s.armed} "
                f"pos={s.position} cooldown={s.cooldown} halted={s.halted}] "
                f"events[arms={s.arms} time_stops={s.time_stops} flatten={s.flatten_events}] "
                f"errors={s.errors}"
            )

    def _log_status(self):
        now = time.time()
        if now - self._last_status_log_ts < self._status_log_interval:
            return
        self._last_status_log_ts = now

        parts: list[str] = []
        for symbol in self.symbols:
            state = self.symbol_states[symbol]
            if state.position_ticket:
                parts.append(f"{symbol}:POSITION({state.position_side})")
            elif state.buy_ticket and state.sell_ticket:
                ttl_left = max(0, int(state.arm_expires_at - now))
                parts.append(f"{symbol}:ARMED(ttl={ttl_left}s)")
            else:
                cd = max(0, int(state.cooldown_until - now))
                if cd > 0:
                    parts.append(f"{symbol}:COOLDOWN({cd}s)")
                else:
                    parts.append(f"{symbol}:IDLE")

        log.info("OCO status | " + " | ".join(parts))
