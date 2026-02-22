from __future__ import annotations

import argparse
import math
import time
from datetime import datetime, timezone

from brokers.mt5 import MT5Broker
from config import settings as cfg
from execution.models import OrderIntent
from execution.order_manager import OrderManager
from risk.guards import RiskConfig, RiskSnapshot, should_risk_off
from storage.state_store import load_state, save_state
from strategy.indicators import adx, atr_pips, trend_direction
from strategy.planner import build_grid_plan
from strategy.state_machine import TransitionInput, transition_phase
from strategy.types import Phase, SymbolRuntime, TrendDirection
from utils.logger import setup_logging


class PhasedGridEngine:
    def __init__(self) -> None:
        self.broker = MT5Broker()
        self.order_mgr = OrderManager(self.broker)
        self.states = load_state(cfg.STATE_PATH)

    def _runtime(self, symbol: str) -> SymbolRuntime:
        if symbol not in self.states:
            self.states[symbol] = SymbolRuntime(symbol=symbol)
        return self.states[symbol]

    def _ingest_realized_pnl(self, symbol: str, state: SymbolRuntime, phase: Phase) -> None:
        now = datetime.now(timezone.utc)
        last_epoch = int(state.metadata.get("last_deal_scan_epoch", 0) or 0)
        if last_epoch <= 0:
            state.metadata["last_deal_scan_epoch"] = int(now.timestamp())
            return

        start = datetime.fromtimestamp(last_epoch, tz=timezone.utc)
        deals = self.broker.history_deals(start, now)

        last_ticket = int(state.metadata.get("last_deal_ticket", 0) or 0)
        max_ticket = last_ticket
        for deal in deals:
            if deal.get("symbol") != symbol:
                continue
            ticket = int(deal.get("ticket", 0) or 0)
            if ticket <= last_ticket:
                continue
            max_ticket = max(max_ticket, ticket)

            pnl = float(deal.get("profit", 0.0) or 0.0)
            pnl += float(deal.get("commission", 0.0) or 0.0)
            pnl += float(deal.get("swap", 0.0) or 0.0)

            comment = str(deal.get("comment", "") or "").lower()
            is_trend_bucket = (
                "cleanup" in comment
                or "unwind" in comment
                or phase in {Phase.TREND_LOCK, Phase.EXHAUSTION_CONFIRM, Phase.GARBAGE_COLLECT, Phase.RISK_OFF}
            )
            if is_trend_bucket:
                state.buffer_pnl += pnl
            else:
                state.chop_pnl += pnl

        state.metadata["last_deal_ticket"] = max_ticket
        state.metadata["last_deal_scan_epoch"] = int(now.timestamp())

    def _ensure_anchor(self, symbol: str, state: SymbolRuntime, lot_size: float) -> None:
        if state.anchor_initialized or not cfg.START_WITH_ANCHOR:
            return

        existing = self.broker.our_positions(symbol)
        if existing:
            state.anchor_initialized = True
            return

        anchor_lot = lot_size * cfg.ANCHOR_LOT_MULT
        self.order_mgr.place_intent(
            OrderIntent(
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                volume=anchor_lot,
                price=0.0,
                comment="phased_grid_anchor_buy",
            )
        )
        self.order_mgr.place_intent(
            OrderIntent(
                symbol=symbol,
                side="SELL",
                order_type="MARKET",
                volume=anchor_lot,
                price=0.0,
                comment="phased_grid_anchor_sell",
            )
        )
        state.anchor_initialized = True

    def _risk_snapshot(self, symbol: str, pip_size: float) -> RiskSnapshot:
        account = self.broker.account_info()
        equity = float(account.get("equity", 0.0) or 0.0)
        balance = float(account.get("balance", 0.0) or 0.0)
        margin = float(account.get("margin", 0.0) or 0.0)
        margin_usage = (margin / equity * 100.0) if equity > 0 else 100.0
        drawdown = ((balance - equity) / balance * 100.0) if balance > 0 else 0.0

        tick = self.broker.symbol_tick(symbol) or {}
        spread_pips = 0.0
        if tick and pip_size > 0:
            spread_pips = (float(tick.get("ask", 0.0)) - float(tick.get("bid", 0.0))) / pip_size

        positions = self.broker.our_positions(symbol)
        buy_lots = sum(float(p.get("volume", 0.0)) for p in positions if int(p.get("type", 0)) == 0)
        sell_lots = sum(float(p.get("volume", 0.0)) for p in positions if int(p.get("type", 0)) == 1)

        return RiskSnapshot(
            spread_pips=float(spread_pips),
            margin_usage_pct=float(margin_usage),
            drawdown_pct=float(drawdown),
            net_delta_lots=float(buy_lots - sell_lots),
        )

    def _step_ticks(self, atr_pips_value: float, spread_pips: float, pip_size: float, point: float) -> int:
        atr_price = atr_pips_value * pip_size
        spread_price = spread_pips * pip_size
        target = max(atr_price * cfg.STEP_ATR_MULT, spread_price * cfg.STEP_SPREAD_MULT)
        if point <= 0:
            return cfg.MIN_STEP_TICKS
        return max(cfg.MIN_STEP_TICKS, int(math.ceil(target / point)))

    @staticmethod
    def _trend_persistence(prev: TrendDirection, current: TrendDirection, count: int) -> int:
        if current == TrendDirection.FLAT:
            return 0
        if current == prev:
            return count + 1
        return 1

    def _build_intents(
        self,
        symbol: str,
        state: SymbolRuntime,
        point: float,
        lot_size: float,
    ) -> list[OrderIntent]:
        step_price = state.step_ticks * point
        if step_price <= 0:
            return []

        plan = build_grid_plan(
            center_price=state.center_price,
            step_price=step_price,
            offset_ratio=cfg.OFFSET_RATIO,
            levels=cfg.MAX_RUNGS_PER_SIDE,
            phase=state.phase,
            trend=state.trend,
        )

        intents: list[OrderIntent] = []
        for entry in plan.long_entries:
            intents.append(
                OrderIntent(
                    symbol=symbol,
                    side="BUY",
                    order_type="LIMIT",
                    volume=lot_size,
                    price=entry,
                    tp=entry + plan.step_price,
                    comment="phased_grid_long",
                )
            )
        for entry in plan.short_entries:
            intents.append(
                OrderIntent(
                    symbol=symbol,
                    side="SELL",
                    order_type="LIMIT",
                    volume=lot_size,
                    price=entry,
                    tp=entry - plan.step_price,
                    comment="phased_grid_short",
                )
            )
        return intents

    def run(self) -> None:
        if not self.broker.connect():
            raise SystemExit("Could not connect MT5")

        account_model = self.broker.account_model()
        if not account_model.get("is_hedging"):
            raise SystemExit("Phased grid requires MT5 hedging account mode")

        for symbol in cfg.SYMBOLS:
            if not self.broker.select_symbol(symbol):
                raise SystemExit(f"Cannot select symbol: {symbol}")

        risk_cfg = RiskConfig(
            max_spread_pips=cfg.MAX_SPREAD_PIPS,
            max_margin_usage_pct=cfg.MAX_MARGIN_USAGE_PCT,
            max_drawdown_pct=cfg.MAX_DRAWDOWN_PCT,
            max_net_delta_lots=cfg.MAX_NET_DELTA_LOTS,
        )

        while True:
            for symbol in cfg.SYMBOLS:
                info = self.broker.symbol_info(symbol)
                tick = self.broker.symbol_tick(symbol)
                rates = self.broker.get_rates(symbol, cfg.TIMEFRAME, cfg.BAR_COUNT)
                if not info or not tick or rates is None or rates.empty:
                    continue

                point = float(info.get("point", 0.0))
                pip_size = self.broker.pip_size(info)
                mid = (float(tick["bid"]) + float(tick["ask"])) / 2.0

                state = self._runtime(symbol)
                if state.center_price <= 0:
                    state.center_price = mid
                self._ensure_anchor(symbol, state, cfg.LOT_SIZE)

                adx_value = adx(rates, cfg.ADX_PERIOD)
                atr_value_pips = atr_pips(rates, cfg.ATR_PERIOD, pip_size)
                trend = trend_direction(rates, cfg.TREND_FAST_EMA, cfg.TREND_SLOW_EMA)
                spread_pips = (float(tick["ask"]) - float(tick["bid"])) / pip_size if pip_size > 0 else 0.0
                step_ticks = self._step_ticks(atr_value_pips, spread_pips, pip_size, point)
                step_price = step_ticks * point

                state.trend_persist_bars = self._trend_persistence(
                    state.trend,
                    trend,
                    state.trend_persist_bars,
                )
                slow_move_trigger = False
                if step_price > 0 and trend != TrendDirection.FLAT:
                    drift_steps = abs(mid - state.center_price) / step_price
                    slow_move_trigger = (
                        drift_steps >= cfg.SLOW_TREND_MIN_MOVE_STEPS
                        and state.trend_persist_bars >= cfg.SLOW_TREND_BARS
                    )

                risk = self._risk_snapshot(symbol, pip_size)
                risk_off = should_risk_off(risk, risk_cfg)

                trend_on = (adx_value >= cfg.TREND_ON_ADX and trend != TrendDirection.FLAT) or slow_move_trigger
                trend_off = adx_value <= cfg.TREND_OFF_ADX or trend == TrendDirection.FLAT
                if trend_off:
                    state.stable_bars += 1
                else:
                    state.stable_bars = 0
                stable = state.stable_bars >= cfg.EXHAUSTION_CONFIRM_BARS

                positions = self.broker.our_positions(symbol)
                buy_count = sum(1 for p in positions if int(p.get("type", 0)) == 0)
                sell_count = sum(1 for p in positions if int(p.get("type", 0)) == 1)
                cleanup_done = buy_count == 0 or sell_count == 0

                prev_phase = state.phase
                state.phase = transition_phase(
                    state.phase,
                    TransitionInput(
                        trend_on=trend_on,
                        trend_off=trend_off,
                        stable=stable,
                        risk_off=risk_off,
                        cleanup_done=cleanup_done,
                    ),
                )

                state.trend = trend
                state.step_ticks = step_ticks
                self._ingest_realized_pnl(symbol, state, state.phase)

                recenter_distance = cfg.RECENTER_MOVE_STEPS * step_ticks * point
                if recenter_distance > 0 and abs(mid - state.center_price) >= recenter_distance:
                    state.center_price = mid

                if state.phase == Phase.GARBAGE_COLLECT:
                    losing_side = "BUY" if trend == TrendDirection.DOWN else "SELL"
                    budget = state.buffer_pnl if cfg.PROTECT_OSCILLATION_BANK else None
                    _, est_realized = self.order_mgr.close_positions(
                        symbol,
                        losing_side,
                        cfg.CLEANUP_CLOSE_COUNT,
                        max_loss_budget=budget,
                    )
                    if est_realized >= 0:
                        state.buffer_pnl += est_realized
                    else:
                        state.buffer_pnl = max(0.0, state.buffer_pnl + est_realized)

                intents = self._build_intents(symbol, state, point, cfg.LOT_SIZE)
                if state.phase != Phase.RISK_OFF:
                    self.order_mgr.sync_pending(
                        symbol=symbol,
                        intents=intents,
                        max_pending=2 * cfg.MAX_RUNGS_PER_SIDE,
                    )
                else:
                    self.order_mgr.sync_pending(symbol=symbol, intents=[], max_pending=0)
                    budget = state.buffer_pnl if cfg.PROTECT_OSCILLATION_BANK else None
                    _, est_realized = self.order_mgr.unwind_positions(
                        symbol,
                        cfg.RISK_OFF_UNWIND_PER_CYCLE,
                        max_loss_budget=budget,
                    )
                    if est_realized >= 0:
                        state.buffer_pnl += est_realized
                    else:
                        state.buffer_pnl = max(0.0, state.buffer_pnl + est_realized)

                if prev_phase != state.phase:
                    print(f"[{symbol}] phase: {prev_phase.value} -> {state.phase.value}")

                state.last_mid = mid

            save_state(cfg.STATE_PATH, self.states)
            time.sleep(cfg.LOOP_SECONDS)


def show_status() -> None:
    broker = MT5Broker()
    if not broker.connect():
        print("ERROR: cannot connect MT5")
        return
    acc = broker.account_info()
    print("=" * 60)
    print("PHASED GRID STATUS")
    print("=" * 60)
    print(f"Account: {acc.get('login')} @ {acc.get('server')}")
    print(f"Balance: {acc.get('balance', 0):,.2f}")
    print(f"Equity:  {acc.get('equity', 0):,.2f}")
    print(f"Margin:  {acc.get('margin', 0):,.2f}")
    print("-" * 60)
    for symbol in cfg.SYMBOLS:
        positions = broker.our_positions(symbol)
        print(f"{symbol}: {len(positions)} open positions")
    broker.disconnect()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Phase-Offset Grid (MT5)")
    parser.add_argument("--status", action="store_true", help="show account status only")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    engine = PhasedGridEngine()
    engine.run()


if __name__ == "__main__":
    main()
