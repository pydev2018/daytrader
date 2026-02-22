from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
import random
import time

import pandas as pd

from risk.guards import RiskConfig, RiskSnapshot, should_risk_off
from sim.config import BacktestConfig
from sim.types import FillEvent, PendingOrder, Position, SymbolSpec
from strategy.indicators import adx, atr_pips, trend_direction
from strategy.planner import build_grid_plan
from strategy.state_machine import TransitionInput, transition_phase
from strategy.types import Phase, SymbolRuntime, TrendDirection


class BacktestEngine:
    def __init__(
        self,
        *,
        config: BacktestConfig,
        market_data: dict[str, pd.DataFrame],
        specs: dict[str, SymbolSpec],
    ) -> None:
        self.cfg = config
        self.market_data = market_data
        self.specs = specs
        self.rng = random.Random(config.random_seed)

        self.balance = config.initial_balance
        self.equity = config.initial_balance
        self.peak_equity = config.initial_balance
        self.max_drawdown_seen_pct = 0.0

        self.states: dict[str, SymbolRuntime] = {
            symbol: SymbolRuntime(symbol=symbol) for symbol in market_data.keys()
        }
        self.pending: dict[str, list[PendingOrder]] = defaultdict(list)
        self.positions: dict[str, list[Position]] = defaultdict(list)
        self.events: list[FillEvent] = []
        self.phase_changes: list[tuple[datetime, str, str, str]] = []
        self._row_lookup: dict[str, dict[datetime, pd.Series]] = {
            symbol: {row["time"]: row for _, row in df.iterrows()}
            for symbol, df in market_data.items()
        }
        self._row_index_lookup: dict[str, dict[datetime, int]] = {
            symbol: {row["time"]: idx for idx, row in df.iterrows()}
            for symbol, df in market_data.items()
        }

    @staticmethod
    def _credit_bank(state: SymbolRuntime, amount: float, trend_bucket: bool) -> None:
        if trend_bucket:
            state.buffer_pnl += amount
        else:
            state.chop_pnl += amount

    @staticmethod
    def _is_countertrend(side: str, trend: TrendDirection) -> bool:
        if trend == TrendDirection.UP:
            return side == "SELL"
        if trend == TrendDirection.DOWN:
            return side == "BUY"
        return False

    def _reserve_risk_cash(self, symbol: str, volume: float, step_price: float) -> float:
        spec = self.specs[symbol]
        step_pips = step_price / max(spec.pip_size, 1e-12)
        return max(0.0, step_pips * spec.pip_value_per_lot * volume * self.cfg.paired_risk_steps)

    @staticmethod
    def _paired_reserved(state: SymbolRuntime) -> float:
        return float(state.metadata.get("paired_reserved", 0.0) or 0.0)

    @staticmethod
    def _set_paired_reserved(state: SymbolRuntime, value: float) -> None:
        state.metadata["paired_reserved"] = max(0.0, float(value))

    def _available_trend_credit(self, state: SymbolRuntime) -> float:
        return max(0.0, state.buffer_pnl - self._paired_reserved(state))

    def _release_paired_reserve(self, state: SymbolRuntime, pos: Position) -> None:
        if pos.paired_reserve <= 0:
            return
        current = self._paired_reserved(state)
        self._set_paired_reserved(state, current - pos.paired_reserve)

    def _build_timeline(self) -> list[datetime]:
        all_times: set[datetime] = set()
        for df in self.market_data.values():
            all_times.update(df["time"].tolist())
        return sorted(all_times)

    def _row_for_time(self, symbol: str, timestamp: datetime) -> pd.Series | None:
        idx = self._row_index_lookup[symbol].get(timestamp)
        if idx is None:
            return None
        return self.market_data[symbol].iloc[idx]

    def _window_to_time(self, symbol: str, timestamp: datetime) -> pd.DataFrame:
        idx = self._row_index_lookup[symbol].get(timestamp)
        if idx is None:
            return self.market_data[symbol].iloc[0:0]
        return self.market_data[symbol].iloc[: idx + 1]

    def _slippage_pips(self, symbol: str, row: pd.Series, trend_on: bool) -> float:
        spec = self.specs[symbol]
        bar_range_pips = (float(row["high"]) - float(row["low"])) / max(spec.pip_size, 1e-12)
        slip = self.cfg.base_slippage_pips
        slip += self.cfg.range_slippage_mult * bar_range_pips
        if trend_on:
            slip += self.cfg.trend_slippage_mult * bar_range_pips
        jitter = self.rng.uniform(0.0, max(0.05, 0.25 * slip))
        return max(0.0, slip + jitter)

    def _spread_pips(self, symbol: str, row: pd.Series) -> float:
        spec = self.specs[symbol]
        spread_points = float(row.get("spread", 0.0) or 0.0)
        return (spread_points * spec.point) / max(spec.pip_size, 1e-12)

    def _commission(self, volume: float) -> float:
        return self.cfg.commission_per_lot_per_side * volume

    def _pip_pnl(self, symbol: str, side: str, entry: float, exit_price: float, volume: float) -> float:
        spec = self.specs[symbol]
        direction = 1.0 if side == "BUY" else -1.0
        pips = direction * (exit_price - entry) / max(spec.pip_size, 1e-12)
        return pips * spec.pip_value_per_lot * volume

    def _unrealized_pnl(self, symbol: str, mid: float) -> float:
        total = 0.0
        for pos in self.positions[symbol]:
            total += self._pip_pnl(symbol, pos.side, pos.entry_price, mid, pos.volume)
        return total

    def _margin_used(self, symbol_rows: dict[str, pd.Series]) -> float:
        total = 0.0
        for symbol, pos_list in self.positions.items():
            if not pos_list:
                continue
            spec = self.specs[symbol]
            row = symbol_rows.get(symbol)
            if row is None:
                continue
            mid = (float(row["high"]) + float(row["low"])) / 2.0
            for pos in pos_list:
                notional = spec.contract_size * pos.volume * mid
                total += notional / max(self.cfg.leverage, 1e-9)
        return total

    def _risk_snapshot(self, symbol: str, row: pd.Series, symbol_rows: dict[str, pd.Series]) -> RiskSnapshot:
        mid = (float(row["high"]) + float(row["low"])) / 2.0
        spread = self._spread_pips(symbol, row)

        total_unrealized = 0.0
        for sym, sym_row in symbol_rows.items():
            sym_mid = (float(sym_row["high"]) + float(sym_row["low"])) / 2.0
            total_unrealized += self._unrealized_pnl(sym, sym_mid)

        self.equity = self.balance + total_unrealized
        self.peak_equity = max(self.peak_equity, self.equity)

        margin = self._margin_used(symbol_rows)
        margin_pct = (margin / self.equity * 100.0) if self.equity > 0 else 100.0
        drawdown_pct = (
            (self.peak_equity - self.equity) / self.peak_equity * 100.0
            if self.peak_equity > 0
            else 0.0
        )
        self.max_drawdown_seen_pct = max(self.max_drawdown_seen_pct, drawdown_pct)

        buys = sum(p.volume for p in self.positions[symbol] if p.side == "BUY")
        sells = sum(p.volume for p in self.positions[symbol] if p.side == "SELL")

        return RiskSnapshot(
            spread_pips=spread,
            margin_usage_pct=margin_pct,
            drawdown_pct=drawdown_pct,
            net_delta_lots=buys - sells,
        )

    def _step_ticks(self, symbol: str, atr_pips_value: float, spread_pips: float) -> int:
        spec = self.specs[symbol]
        atr_price = atr_pips_value * spec.pip_size
        spread_price = spread_pips * spec.pip_size
        target = max(atr_price * self.cfg.step_atr_mult, spread_price * self.cfg.step_spread_mult)
        if spec.point <= 0:
            return self.cfg.min_step_ticks
        return max(self.cfg.min_step_ticks, int((target / spec.point) + 0.999999))

    def _trend_persistence(self, prev: TrendDirection, curr: TrendDirection, count: int) -> int:
        if curr == TrendDirection.FLAT:
            return 0
        if prev == curr:
            return count + 1
        return 1

    def _apply_swap(self, symbol: str, now: datetime, delta_days: float) -> None:
        if delta_days <= 0:
            return
        for pos in self.positions[symbol]:
            if pos.side == "BUY":
                swap = self.cfg.long_swap_per_lot_per_day * pos.volume * delta_days
            else:
                swap = self.cfg.short_swap_per_lot_per_day * pos.volume * delta_days
            self.balance -= swap

    def _maybe_place_anchor(self, symbol: str, state: SymbolRuntime, row: pd.Series) -> None:
        if not self.cfg.start_with_anchor or state.anchor_initialized:
            return
        if self.positions[symbol]:
            state.anchor_initialized = True
            return

        mid = (float(row["open"]) + float(row["close"])) / 2.0
        volume = self.cfg.lot_size * self.cfg.anchor_lot_mult
        self.positions[symbol].append(
            Position(symbol=symbol, side="BUY", entry_price=mid, tp=mid, volume=volume, opened_at=row["time"], is_anchor=True, entry_phase="INIT")
        )
        self.positions[symbol].append(
            Position(symbol=symbol, side="SELL", entry_price=mid, tp=mid, volume=volume, opened_at=row["time"], is_anchor=True, entry_phase="INIT")
        )
        fee = self._commission(volume) * 2
        self.balance -= fee
        state.anchor_initialized = True

    def _create_pending_from_plan(self, symbol: str, state: SymbolRuntime, row: pd.Series) -> None:
        spec = self.specs[symbol]
        step_price = state.step_ticks * spec.point
        if step_price <= 0:
            self.pending[symbol] = []
            return

        plan = build_grid_plan(
            center_price=state.center_price,
            step_price=step_price,
            offset_ratio=self.cfg.offset_ratio,
            levels=self.cfg.max_rungs_per_side,
            phase=state.phase,
            trend=state.trend,
        )

        new_orders: list[PendingOrder] = []
        for price in plan.long_entries:
            new_orders.append(
                PendingOrder(
                    symbol=symbol,
                    side="BUY",
                    price=price,
                    tp=price + step_price,
                    volume=self.cfg.lot_size,
                    active_from=row["time"],
                )
            )
        for price in plan.short_entries:
            new_orders.append(
                PendingOrder(
                    symbol=symbol,
                    side="SELL",
                    price=price,
                    tp=price - step_price,
                    volume=self.cfg.lot_size,
                    active_from=row["time"],
                )
            )
        self.pending[symbol] = new_orders[: 2 * self.cfg.max_rungs_per_side]

    def _fill_entries(
        self,
        symbol: str,
        row: pd.Series,
        trend_on: bool,
        state: SymbolRuntime,
        step_price: float,
    ) -> None:
        spec = self.specs[symbol]
        remaining: list[PendingOrder] = []
        for order in self.pending[symbol]:
            if row["time"] < order.active_from:
                remaining.append(order)
                continue

            touched = False
            if order.side == "BUY" and float(row["low"]) <= order.price:
                touched = True
            if order.side == "SELL" and float(row["high"]) >= order.price:
                touched = True

            if not touched:
                remaining.append(order)
                continue

            spread_pips = self._spread_pips(symbol, row)
            slip_pips = self._slippage_pips(symbol, row, trend_on)
            half_spread = 0.5 * spread_pips * spec.pip_size
            slip_price = slip_pips * spec.pip_size
            if order.side == "BUY":
                fill_price = order.price + half_spread + slip_price
            else:
                fill_price = order.price - half_spread - slip_price

            paired_reserve = 0.0
            if (
                self.cfg.protect_oscillation_bank
                and self.cfg.paired_ledger_enabled
                and self._is_countertrend(order.side, state.trend)
            ):
                paired_reserve = self._reserve_risk_cash(symbol, order.volume, step_price)
                if paired_reserve > self._available_trend_credit(state):
                    remaining.append(order)
                    continue

                self._set_paired_reserved(state, self._paired_reserved(state) + paired_reserve)

            fee = self._commission(order.volume)
            self.balance -= fee
            self.positions[symbol].append(
                Position(
                    symbol=symbol,
                    side=order.side,
                    entry_price=fill_price,
                    tp=order.tp,
                    volume=order.volume,
                    opened_at=row["time"],
                    entry_phase=state.phase.value,
                    paired_reserve=paired_reserve,
                )
            )
            self.events.append(
                FillEvent(
                    timestamp=row["time"],
                    symbol=symbol,
                    side=order.side,
                    event_type="ENTRY",
                    price=fill_price,
                    volume=order.volume,
                    pnl=0.0,
                    fee=fee,
                    slip_pips=slip_pips,
                )
            )
        self.pending[symbol] = remaining

    def _fill_tps(self, symbol: str, row: pd.Series, trend_on: bool, state: SymbolRuntime) -> None:
        spec = self.specs[symbol]
        survivors: list[Position] = []
        for pos in self.positions[symbol]:
            if pos.is_anchor:
                survivors.append(pos)
                continue

            touched = False
            if pos.side == "BUY" and float(row["high"]) >= pos.tp:
                touched = True
            if pos.side == "SELL" and float(row["low"]) <= pos.tp:
                touched = True

            if not touched:
                survivors.append(pos)
                continue

            spread_pips = self._spread_pips(symbol, row)
            slip_pips = self._slippage_pips(symbol, row, trend_on)
            half_spread = 0.5 * spread_pips * spec.pip_size
            slip_price = slip_pips * spec.pip_size
            if pos.side == "BUY":
                exit_price = pos.tp - half_spread - slip_price
            else:
                exit_price = pos.tp + half_spread + slip_price

            pnl = self._pip_pnl(symbol, pos.side, pos.entry_price, exit_price, pos.volume)
            fee = self._commission(pos.volume)
            self.balance += pnl - fee
            realized = pnl - fee

            trend_bucket = (
                state.phase in {Phase.TREND_LOCK, Phase.EXHAUSTION_CONFIRM, Phase.GARBAGE_COLLECT, Phase.RISK_OFF}
                or pos.entry_phase in {Phase.TREND_LOCK.value, Phase.EXHAUSTION_CONFIRM.value}
            )
            self._credit_bank(state, realized, trend_bucket)
            self._release_paired_reserve(state, pos)

            self.events.append(
                FillEvent(
                    timestamp=row["time"],
                    symbol=symbol,
                    side=pos.side,
                    event_type="TP_EXIT",
                    price=exit_price,
                    volume=pos.volume,
                    pnl=pnl,
                    fee=fee,
                    slip_pips=slip_pips,
                )
            )
        self.positions[symbol] = survivors

    def _risk_off_unwind(self, symbol: str, row: pd.Series, state: SymbolRuntime) -> int:
        if not self.positions[symbol]:
            return 0
        spec = self.specs[symbol]
        sorted_positions = sorted(
            [p for p in self.positions[symbol] if not p.is_anchor],
            key=lambda p: self._pip_pnl(symbol, p.side, p.entry_price, float(row["close"]), p.volume),
        )
        to_close: list[Position] = []
        loss_budget = state.buffer_pnl if self.cfg.protect_oscillation_bank else None
        for pos in sorted_positions:
            if len(to_close) >= self.cfg.risk_off_unwind_per_cycle:
                break
            est_pnl = self._pip_pnl(symbol, pos.side, pos.entry_price, float(row["close"]), pos.volume)
            if loss_budget is not None and est_pnl < 0 and abs(est_pnl) > loss_budget:
                continue
            to_close.append(pos)
            if loss_budget is not None and est_pnl < 0:
                loss_budget = max(0.0, loss_budget - abs(est_pnl))

        if not to_close:
            return 0

        survivors = []
        close_set = set(id(p) for p in to_close)
        for pos in self.positions[symbol]:
            if id(pos) in close_set:
                spread_pips = self._spread_pips(symbol, row)
                slip_pips = self._slippage_pips(symbol, row, trend_on=True)
                half_spread = 0.5 * spread_pips * spec.pip_size
                slip_price = slip_pips * spec.pip_size
                if pos.side == "BUY":
                    exit_price = float(row["close"]) - half_spread - slip_price
                else:
                    exit_price = float(row["close"]) + half_spread + slip_price

                pnl = self._pip_pnl(symbol, pos.side, pos.entry_price, exit_price, pos.volume)
                fee = self._commission(pos.volume)
                self.balance += pnl - fee
                realized = pnl - fee
                self._credit_bank(state, realized, trend_bucket=True)
                self._release_paired_reserve(state, pos)
                self.events.append(
                    FillEvent(
                        timestamp=row["time"],
                        symbol=symbol,
                        side=pos.side,
                        event_type="RISK_UNWIND",
                        price=exit_price,
                        volume=pos.volume,
                        pnl=pnl,
                        fee=fee,
                        slip_pips=slip_pips,
                    )
                )
            else:
                survivors.append(pos)
        self.positions[symbol] = survivors
        return len(to_close)

    def _garbage_collect(self, symbol: str, row: pd.Series, trend: TrendDirection, state: SymbolRuntime) -> int:
        candidates = []
        for pos in self.positions[symbol]:
            if pos.is_anchor:
                continue
            if trend == TrendDirection.DOWN and pos.side == "BUY":
                candidates.append(pos)
            elif trend != TrendDirection.DOWN and pos.side == "SELL":
                candidates.append(pos)

        to_close: list[Position] = []
        loss_budget = state.buffer_pnl if self.cfg.protect_oscillation_bank else None
        for pos in candidates:
            if len(to_close) >= self.cfg.cleanup_close_count:
                break
            est_pnl = self._pip_pnl(symbol, pos.side, pos.entry_price, float(row["close"]), pos.volume)
            if loss_budget is not None and est_pnl < 0 and abs(est_pnl) > loss_budget:
                continue
            to_close.append(pos)
            if loss_budget is not None and est_pnl < 0:
                loss_budget = max(0.0, loss_budget - abs(est_pnl))

        if not to_close:
            return 0
        spec = self.specs[symbol]

        survivors = []
        close_set = set(id(p) for p in to_close)
        for pos in self.positions[symbol]:
            if id(pos) in close_set:
                spread_pips = self._spread_pips(symbol, row)
                slip_pips = self._slippage_pips(symbol, row, trend_on=False)
                half_spread = 0.5 * spread_pips * spec.pip_size
                slip_price = slip_pips * spec.pip_size
                if pos.side == "BUY":
                    exit_price = float(row["close"]) - half_spread - slip_price
                else:
                    exit_price = float(row["close"]) + half_spread + slip_price
                pnl = self._pip_pnl(symbol, pos.side, pos.entry_price, exit_price, pos.volume)
                fee = self._commission(pos.volume)
                self.balance += pnl - fee
                realized = pnl - fee
                self._credit_bank(state, realized, trend_bucket=True)
                self._release_paired_reserve(state, pos)
                self.events.append(
                    FillEvent(
                        timestamp=row["time"],
                        symbol=symbol,
                        side=pos.side,
                        event_type="GARBAGE_COLLECT",
                        price=exit_price,
                        volume=pos.volume,
                        pnl=pnl,
                        fee=fee,
                        slip_pips=slip_pips,
                    )
                )
            else:
                survivors.append(pos)
        self.positions[symbol] = survivors
        return len(to_close)

    def run(self) -> dict:
        timeline = self._build_timeline()
        if not timeline:
            raise RuntimeError("No market data available for backtest")

        started = time.perf_counter()

        risk_cfg = RiskConfig(
            max_spread_pips=self.cfg.max_spread_pips,
            max_margin_usage_pct=self.cfg.max_margin_usage_pct,
            max_drawdown_pct=self.cfg.max_drawdown_pct,
            max_net_delta_lots=self.cfg.max_net_delta_lots,
        )

        prev_ts = timeline[0]
        total_steps = len(timeline)
        for step_index, ts in enumerate(timeline, start=1):
            delta_days = (ts - prev_ts).total_seconds() / 86400.0
            prev_ts = ts

            symbol_rows: dict[str, pd.Series] = {}
            for symbol in self.market_data:
                row = self._row_for_time(symbol, ts)
                if row is not None:
                    symbol_rows[symbol] = row

            for symbol, row in symbol_rows.items():
                state = self.states[symbol]
                window = self._window_to_time(symbol, ts)
                if len(window) < max(self.cfg.atr_period, self.cfg.adx_period, self.cfg.trend_slow_ema) + 5:
                    continue

                self._apply_swap(symbol, ts, delta_days)
                self._maybe_place_anchor(symbol, state, row)

                spec = self.specs[symbol]
                mid = (float(row["open"]) + float(row["close"])) / 2.0
                if state.center_price <= 0:
                    state.center_price = mid

                adx_value = adx(window, self.cfg.adx_period)
                atr_value = atr_pips(window, self.cfg.atr_period, spec.pip_size)
                trend = trend_direction(window, self.cfg.trend_fast_ema, self.cfg.trend_slow_ema)
                spread = self._spread_pips(symbol, row)
                step_ticks = self._step_ticks(symbol, atr_value, spread)
                step_price = step_ticks * spec.point

                state.trend_persist_bars = self._trend_persistence(state.trend, trend, state.trend_persist_bars)

                risk = self._risk_snapshot(symbol, row, symbol_rows)
                risk_off = should_risk_off(risk, risk_cfg)

                slow_trigger = False
                if step_price > 0 and trend != TrendDirection.FLAT:
                    drift_steps = abs(mid - state.center_price) / step_price
                    slow_trigger = (
                        drift_steps >= self.cfg.slow_trend_min_move_steps
                        and state.trend_persist_bars >= self.cfg.slow_trend_bars
                    )

                trend_on = (adx_value >= self.cfg.trend_on_adx and trend != TrendDirection.FLAT) or slow_trigger
                trend_off = adx_value <= self.cfg.trend_off_adx or trend == TrendDirection.FLAT

                if trend_off:
                    state.stable_bars += 1
                else:
                    state.stable_bars = 0
                stable = state.stable_bars >= self.cfg.exhaustion_confirm_bars

                buy_count = sum(1 for p in self.positions[symbol] if p.side == "BUY")
                sell_count = sum(1 for p in self.positions[symbol] if p.side == "SELL")
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

                if prev_phase != state.phase:
                    self.phase_changes.append((ts, symbol, prev_phase.value, state.phase.value))

                recenter_dist = self.cfg.recenter_move_steps * step_price
                if recenter_dist > 0 and abs(mid - state.center_price) >= recenter_dist:
                    state.center_price = mid

                self._fill_tps(symbol, row, trend_on, state)

                if state.phase == Phase.GARBAGE_COLLECT:
                    self._garbage_collect(symbol, row, trend, state)

                if state.phase == Phase.RISK_OFF:
                    self.pending[symbol] = []
                    self._risk_off_unwind(symbol, row, state)
                else:
                    self._create_pending_from_plan(symbol, state, row)
                    self._fill_entries(symbol, row, trend_on, state, step_price)

                state.last_mid = mid

            if (
                self.cfg.progress_every_steps > 0
                and step_index % self.cfg.progress_every_steps == 0
            ):
                elapsed = time.perf_counter() - started
                pct = 100.0 * step_index / max(total_steps, 1)
                print(
                    f"[BACKTEST] Progress {step_index}/{total_steps} "
                    f"({pct:.1f}%) elapsed={elapsed:.1f}s equity={self.equity:.2f}"
                )

        final_unrealized = 0.0
        for symbol, pos_list in self.positions.items():
            if not pos_list:
                continue
            df = self.market_data[symbol]
            if df.empty:
                continue
            last_mid = (float(df.iloc[-1]["open"]) + float(df.iloc[-1]["close"])) / 2.0
            final_unrealized += self._unrealized_pnl(symbol, last_mid)

        final_equity = self.balance + final_unrealized
        total_return_pct = ((final_equity - self.cfg.initial_balance) / self.cfg.initial_balance) * 100.0

        events_by_symbol = defaultdict(int)
        pnl_by_symbol = defaultdict(float)
        for ev in self.events:
            events_by_symbol[ev.symbol] += 1
            pnl_by_symbol[ev.symbol] += ev.pnl - ev.fee

        cfg_payload = asdict(self.cfg)
        cfg_payload["start"] = self.cfg.start.isoformat()
        cfg_payload["end"] = self.cfg.end.isoformat()

        return {
            "config": cfg_payload,
            "initial_balance": self.cfg.initial_balance,
            "final_balance": self.balance,
            "final_equity": final_equity,
            "max_drawdown_pct": self.max_drawdown_seen_pct,
            "total_return_pct": total_return_pct,
            "events": [
                {
                    **asdict(e),
                    "timestamp": e.timestamp.isoformat(),
                }
                for e in self.events
            ],
            "phase_changes": [
                {"time": t.isoformat(), "symbol": s, "from": f, "to": to}
                for t, s, f, to in self.phase_changes
            ],
            "event_count_by_symbol": dict(events_by_symbol),
            "net_pnl_by_symbol": dict(pnl_by_symbol),
            "open_positions": {
                s: [
                    {
                        **asdict(p),
                        "opened_at": p.opened_at.isoformat(),
                    }
                    for p in plist
                ]
                for s, plist in self.positions.items()
                if plist
            },
            "capital_buckets": {
                s: {
                    "oscillation_bank": self.states[s].chop_pnl,
                    "trend_buffer": self.states[s].buffer_pnl,
                    "paired_reserved": float(self.states[s].metadata.get("paired_reserved", 0.0) or 0.0),
                    "paired_available": max(
                        0.0,
                        self.states[s].buffer_pnl - float(self.states[s].metadata.get("paired_reserved", 0.0) or 0.0),
                    ),
                }
                for s in self.states
            },
        }
