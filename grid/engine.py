"""
Grid trading engine orchestration.

Full automated flow:
1. SCAN    — Score all symbols in the universe for grid suitability
2. SELECT  — Pick the top N symbols above minimum score threshold
3. DEPLOY  — Build and manage grids on selected symbols
4. MONITOR — Every SCAN_INTERVAL, re-scan and rotate if better targets emerge
5. RETIRE  — Wind down grids on symbols that are no longer suitable

The engine operates on a tick-by-tick basis (every GRID_LOOP_SECONDS),
but the *effective timeframe* is determined by the parameter constellation:
- ANCHOR_HALFLIFE_SECONDS controls how fast the center adapts
- VOL_HORIZON_SECONDS controls what oscillation horizon spacing targets
- VOL_EWMA_LAMBDA + reference_dt controls the vol estimation smoothness
"""

from __future__ import annotations

import signal
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from statistics import median

import MetaTrader5 as mt5

from alerts.telegram import TelegramAlerter
from brokers.mt5 import MT5Broker
from config import settings as cfg
from execution.order_manager import OrderManager, FillEvent
from grid.anchor import EmaAnchor
from grid.orders import build_rungs, desired_orders, update_rung_on_fill
from grid.regime import classify_regime, compute_trend_z, TrendRegimeFilter
from grid.scanner import GridScanner, FX_UNIVERSE, SymbolScore
from grid.sizing import size_for_rung
from grid.spacing import VolEstimator, compute_spacing
from grid.state import GridState, load_state, new_state, save_state
from risk.grid_risk import GridRiskManager
from utils.logger import get_logger

log = get_logger("grid_engine")

_shutdown_requested = False
HISTORY_LEN = 250

# ─── Market hours (FX) ──────────────────────────────────────────────────
# FX market: Sunday ~22:00 UTC to Friday ~21:55 UTC
# We add buffers: don't trade for 15 min after open (gap protection)
# and stop new entries 10 min before close (pre-weekend safety)
FRIDAY_CLOSE_HOUR = 21      # Friday 21:00 UTC = stop new entries
FRIDAY_CLOSE_MINUTE = 50
SUNDAY_OPEN_HOUR = 22       # Sunday 22:00 UTC
SUNDAY_OPEN_WARMUP_MINUTES = 15  # wait 15 min after open for spreads to settle


def _is_market_open() -> bool:
    """Check if FX market is currently open and safe to trade.

    Returns False during:
    - Saturday (all day)
    - Sunday before 22:15 UTC (market hasn't opened + warmup)
    - Friday after 21:50 UTC (close imminent)
    """
    now = datetime.now(timezone.utc)
    weekday = now.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday

    # Saturday: always closed
    if weekday == 5:
        return False

    # Sunday: closed until 22:00 + warmup
    if weekday == 6:
        open_minute = SUNDAY_OPEN_HOUR * 60 + SUNDAY_OPEN_WARMUP_MINUTES
        current_minute = now.hour * 60 + now.minute
        return current_minute >= open_minute

    # Friday: stop before close
    if weekday == 4:
        close_minute = FRIDAY_CLOSE_HOUR * 60 + FRIDAY_CLOSE_MINUTE
        current_minute = now.hour * 60 + now.minute
        return current_minute < close_minute

    # Monday-Thursday: always open
    return True


def _is_pre_weekend_wind_down() -> bool:
    """Friday after 21:00 UTC — cancel entries, keep exits only."""
    now = datetime.now(timezone.utc)
    if now.weekday() == 4 and now.hour >= FRIDAY_CLOSE_HOUR:
        return True
    return False


def _signal_handler(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    log.info("Shutdown signal received - finishing current cycle ...")


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


class GridEngine:
    def __init__(self):
        self.broker = MT5Broker()
        self.alerter = TelegramAlerter(
            bot_token=cfg.TELEGRAM_BOT_TOKEN,
            chat_id=cfg.TELEGRAM_CHAT_ID,
        )
        self.order_mgr = OrderManager(self.broker)
        self.risk_mgr = GridRiskManager(self.broker)
        self.scanner = GridScanner(self.broker)
        self.states: dict[str, GridState] = load_state()
        self.anchors: dict[str, EmaAnchor] = {}
        self.vol_fast: dict[str, VolEstimator] = {}
        self.vol_slow: dict[str, VolEstimator] = {}
        self.price_history: dict[str, deque] = {}
        self.spread_history: dict[str, deque] = {}
        self.trend_filters: dict[str, TrendRegimeFilter] = {}
        self.last_sync: dict[str, float] = {}
        self.account_model: dict[str, object] = {}

        # Scanner state
        self._active_symbols: list[str] = []
        self._retiring_symbols: list[str] = []  # symbols winding down (exits only)
        self._last_scan_ts: float = 0.0
        self._scan_results: list[SymbolScore] = []
        # Status logging
        self._last_status_log_ts: float = 0.0
        self._status_log_interval: float = 300.0  # every 5 minutes
        self._fill_count: int = 0
        self._session_pnl: float = 0.0
        self._entry_pause_until: dict[str, float] = {}

    def start(self):
        log.info("=" * 70)
        log.info("  GRID TRADING SYSTEM - Starting")
        log.info("=" * 70)

        if not self.broker.connect():
            log.error("Failed to connect to MT5 - exiting")
            return

        acc = self.broker.account_info()
        log.info(
            f"Account: {acc.get('login')} | "
            f"Balance: ${acc.get('balance', 0):,.2f} | "
            f"Equity: ${acc.get('equity', 0):,.2f} | "
            f"Leverage: 1:{acc.get('leverage', 0)}"
        )

        self.account_model = self.broker.account_model()
        log.info(
            f"Account model: netting={self.account_model.get('is_netting')} "
            f"hedging={self.account_model.get('is_hedging')} "
            f"fifo={self.account_model.get('fifo_close')}"
        )
        if not self.account_model.get("is_hedging"):
            log.error("Hedging account required — refusing to start on netting/FIFO")
            try:
                bal = acc.get("balance", 0.0)
                self.alerter.safety_event(
                    "ACCOUNT_MODEL",
                    "Non-hedging account detected — TP-by-position regime requires hedging",
                    bal,
                )
            except Exception:
                pass
            self.broker.disconnect()
            return

        # ── Initial symbol selection ─────────────────────────────────────
        if cfg.AUTO_SCAN_ENABLED:
            self._run_scan()
        else:
            # Manual mode: use GRID_SYMBOLS from .env
            self._active_symbols = list(cfg.GRID_SYMBOLS)
            log.info(f"Auto-scan disabled. Using configured symbols: "
                     f"{', '.join(self._active_symbols)}")

        if not self._active_symbols:
            log.error("No tradeable symbols found - exiting")
            self.broker.disconnect()
            return

        # ── Startup cleanup: cancel ALL orphaned orders ──────────────────
        # On a fresh start or restart after crash, there may be pending
        # orders from a previous grid_id that our sync logic won't see.
        # Cancel everything with our magic number to start clean.
        self._cancel_all_orphaned_orders()

        self._init_symbols(self._active_symbols)

        # ── Adopt orphaned positions ─────────────────────────────────────
        # After crash/restart, open positions with our magic number need
        # to be adopted into the grid with proper exit orders, not abandoned.
        self._adopt_orphaned_positions()
        self.alerter.bot_status(
            "STARTED",
            f"Active: {', '.join(self._active_symbols)}"
            + (f" (auto-scan every {cfg.SCAN_INTERVAL_SECONDS}s)"
               if cfg.AUTO_SCAN_ENABLED else " (manual)"),
        )

        self._main_loop()

    # =====================================================================
    #  SCANNING & SYMBOL ROTATION
    # =====================================================================

    def _get_scan_universe(self) -> list[str]:
        """Determine which symbols to scan."""
        if cfg.SCAN_UNIVERSE:
            return cfg.SCAN_UNIVERSE
        if cfg.GRID_SYMBOLS:
            # If user set specific symbols but also enabled scan,
            # scan a wider universe but always include their picks
            return list(set(cfg.GRID_SYMBOLS + FX_UNIVERSE))
        return FX_UNIVERSE

    def _run_scan(self):
        """Scan the universe and select the best symbols."""
        universe = self._get_scan_universe()
        log.info(f"Scanning {len(universe)} symbols ...")

        results = self.scanner.scan(
            universe,
            timeframe=cfg.SCAN_TIMEFRAME,
            bar_count=cfg.SCAN_BAR_COUNT,
        )
        self._scan_results = results
        self._last_scan_ts = time.time()

        if not results:
            log.warning("Scan returned no results")
            return

        # Log all results
        for r in results:
            log.info(
                f"  SCAN: {r.symbol:<10} score={r.score:>5.1f} "
                f"({r.verdict}) mr={r.mean_reversion:.2f} "
                f"ce={r.cost_efficiency:.2f} vr={r.vol_regime:.2f} "
                f"h={r.details.get('hurst', 0):.3f}"
            )

        # Select top N above minimum score
        candidates = [r for r in results if r.score >= cfg.MIN_SCAN_SCORE]
        new_symbols = [r.symbol for r in candidates[:cfg.MAX_ACTIVE_SYMBOLS]]

        if not new_symbols:
            log.warning(
                f"No symbols scored above {cfg.MIN_SCAN_SCORE}. "
                f"Best: {results[0].symbol}={results[0].score:.1f}. "
                f"Keeping current symbols."
            )
            return

        # Detect changes
        added = set(new_symbols) - set(self._active_symbols)
        removed = set(self._active_symbols) - set(new_symbols)

        if added or removed:
            log.info(
                f"Symbol rotation: active={new_symbols} "
                f"added={added or 'none'} removed={removed or 'none'}"
            )
            self.alerter.bot_status(
                "SCAN UPDATE",
                f"Active: {', '.join(new_symbols)}\n"
                + (f"Added: {', '.join(added)}\n" if added else "")
                + (f"Removed: {', '.join(removed)}" if removed else ""),
            )

            # Retire removed symbols (wind down gracefully)
            for sym in removed:
                self._retire_symbol(sym)

            # Initialize new symbols
            if added:
                self._init_symbols(list(added))

            self._active_symbols = new_symbols
        else:
            log.info(f"Scan complete. No rotation needed. Active: {self._active_symbols}")

    def _retire_symbol(self, symbol: str):
        """Gracefully wind down a symbol's grid.

        1. Cancel all ENTRY orders (no new positions)
        2. Keep EXIT orders alive (protect open positions)
        3. Move symbol to _retiring_symbols list so the main loop
           continues to monitor fills and manage exits until flat.
        """
        state = self.states.get(symbol)
        if state is None:
            return

        # Check if we have any open positions (EXIT rungs)
        exit_rungs = [r for r in state.rungs if r.state == "EXIT"]
        has_positions = len(exit_rungs) > 0

        if has_positions:
            log.info(
                f"Retiring {symbol}: {len(exit_rungs)} open positions — "
                f"moving to wind-down (exits kept alive)"
            )
            # Build desired orders with ONLY exit orders (no entries)
            exit_orders = desired_orders(
                symbol=symbol,
                grid_id=state.grid_id,
                rungs=state.rungs,
                entry_sizes={},  # empty = no entries
                allow_entries=False,
            )

            sym_info = self.broker.symbol_info(symbol) or {}
            tick_size = sym_info.get("trade_tick_size") or sym_info.get("point", 0.0)
            if tick_size > 0:
                limit_orders = int(self.account_model.get("limit_orders", 0) or 0)
                max_pending = (
                    min(cfg.MAX_PENDING_ORDERS_PER_SYMBOL, limit_orders)
                    if limit_orders > 0
                    else cfg.MAX_PENDING_ORDERS_PER_SYMBOL
                )
                tick = self.broker.symbol_tick(symbol)
                mid = (tick["bid"] + tick["ask"]) / 2.0 if tick else 0.0
                # Sync = cancels entries, keeps/places exits
                self.order_mgr.sync_orders(
                    symbol=symbol,
                    desired=exit_orders,
                    grid_id=state.grid_id,
                    tick_size=tick_size,
                    requote_ticks=cfg.REQUOTE_THRESHOLD_TICKS,
                    max_pending=max_pending,
                    current_price=mid,
                )

            state.paused = True
            state.pause_reason = "retiring"

            # Add to retiring list so main loop keeps monitoring
            if symbol not in self._retiring_symbols:
                self._retiring_symbols.append(symbol)
        else:
            log.info(f"Retiring {symbol}: no open positions — clean removal")
            # No positions, just cancel everything
            sym_info = self.broker.symbol_info(symbol) or {}
            tick_size = sym_info.get("trade_tick_size") or sym_info.get("point", 0.0)
            if tick_size > 0:
                limit_orders = int(self.account_model.get("limit_orders", 0) or 0)
                max_pending = (
                    min(cfg.MAX_PENDING_ORDERS_PER_SYMBOL, limit_orders)
                    if limit_orders > 0
                    else cfg.MAX_PENDING_ORDERS_PER_SYMBOL
                )
                self.order_mgr.sync_orders(
                    symbol=symbol,
                    desired=[],
                    grid_id=state.grid_id,
                    tick_size=tick_size,
                    requote_ticks=cfg.REQUOTE_THRESHOLD_TICKS,
                    max_pending=max_pending,
                )
            state.paused = True
            state.pause_reason = "retired"

    def _process_retiring_symbol(self, symbol: str):
        """Monitor a retiring symbol until all positions are closed.

        - Detects fills on exit orders
        - When all EXIT rungs are closed, removes symbol from retiring list
        - No new entries are ever placed
        """
        state = self.states.get(symbol)
        if state is None:
            self._retiring_symbols.remove(symbol)
            return

        sym_info = self.broker.symbol_info(symbol) or {}
        tick_size = sym_info.get("trade_tick_size") or sym_info.get("point", 0.0)
        grid_spacing = state.spacing if state.spacing else tick_size * 10

        # Detect fills (exit orders completing)
        fills = self.order_mgr.detect_fills(
            symbol, state.last_deal_time, state.grid_id, state.last_deal_id
        )
        if fills:
            equity = self.broker.account_equity()
            latest_time = state.last_deal_time
            latest_deal_id = state.last_deal_id
            for fill in fills:
                rung = next(
                    (r for r in state.rungs if r.rung_id == fill.rung_id), None
                )
                if not rung:
                    continue
                update_rung_on_fill(rung, fill.price, grid_spacing, fill.time_iso)
                latest_time = max(latest_time, fill.time_iso)
                latest_deal_id = max(latest_deal_id, fill.deal_id)
                self.order_mgr.journal.log_fill(
                    fill, equity=equity, inventory_lots=state.inventory_lots
                )
                log.info(
                    f"{symbol} [retiring]: exit filled {fill.rung_id} "
                    f"at {fill.price}"
                )
            state.last_deal_time = latest_time
            state.last_deal_id = latest_deal_id

        # Check: are all positions closed?
        exit_rungs = [r for r in state.rungs if r.state == "EXIT"]
        if not exit_rungs:
            # All positions closed — fully retired
            log.info(f"{symbol}: all positions closed — fully retired")
            # Cancel any remaining orders
            if tick_size > 0:
                limit_orders = int(self.account_model.get("limit_orders", 0) or 0)
                max_pending = (
                    min(cfg.MAX_PENDING_ORDERS_PER_SYMBOL, limit_orders)
                    if limit_orders > 0
                    else cfg.MAX_PENDING_ORDERS_PER_SYMBOL
                )
                self.order_mgr.sync_orders(
                    symbol=symbol,
                    desired=[],
                    grid_id=state.grid_id,
                    tick_size=tick_size,
                    requote_ticks=cfg.REQUOTE_THRESHOLD_TICKS,
                    max_pending=max_pending,
                )
            state.paused = True
            state.pause_reason = "retired"
            self._retiring_symbols.remove(symbol)
            self.alerter.bot_status(
                "RETIRE COMPLETE", f"{symbol}: all positions closed"
            )

    def _attach_position_to_rung(
        self,
        symbol: str,
        state: GridState,
        side: str,
        open_price: float,
        volume: float,
        ticket: int,
        grid_center: float,
        spacing: float,
        now_iso: str,
        source: str,
    ):
        """Attach an open position to a rung and ensure TP is set."""
        if spacing <= 0 or open_price <= 0 or volume <= 0 or not ticket:
            return None

        # 1) Already tracked by ticket
        for rung in state.rungs:
            if rung.order_ticket == ticket:
                return rung

        # 2) Match existing EXIT rung with missing ticket
        for rung in state.rungs:
            if (
                rung.state == "EXIT"
                and rung.order_ticket is None
                and rung.fill_price is not None
                and abs(rung.fill_price - open_price) < spacing * 0.3
                and abs(rung.size - volume) < 0.015
            ):
                rung.order_ticket = ticket
                rung.last_update = now_iso
                if rung.exit_price is None:
                    rung.exit_price = (
                        open_price + spacing if side == "BUY" else open_price - spacing
                    )
                if rung.exit_price:
                    self.order_mgr.set_exit_tp(symbol, ticket, rung.exit_price)
                log.info(
                    f"{symbol}: ATTACHED {source} to EXIT rung {rung.rung_id} "
                    f"ticket={ticket}"
                )
                return rung

        # 3) Match existing ENTRY rung (intended entry price)
        for rung in state.rungs:
            if (
                rung.state == "ENTRY"
                and abs(rung.entry_price - open_price) < spacing * 0.3
                and abs(rung.size - volume) < 0.015
            ):
                update_rung_on_fill(rung, open_price, spacing, now_iso)
                rung.size = volume
                rung.order_ticket = ticket
                if rung.exit_price:
                    self.order_mgr.set_exit_tp(symbol, ticket, rung.exit_price)
                log.info(
                    f"{symbol}: ATTACHED {source} to ENTRY rung {rung.rung_id} "
                    f"ticket={ticket}"
                )
                return rung

        # 4) Create a new adopted rung
        if grid_center and spacing > 0:
            level_index = int(round((open_price - grid_center) / spacing))
        else:
            level_index = 0
        existing_ids = {r.rung_id for r in state.rungs}
        rung_id = f"A{ticket}"
        while rung_id in existing_ids:
            rung_id = f"A{len(existing_ids) + 1}"

        from grid.state import GridRung
        exit_price = open_price + spacing if side == "BUY" else open_price - spacing
        new_rung = GridRung(
            rung_id=rung_id,
            level_index=level_index,
            entry_side=side,
            state="EXIT",
            entry_price=open_price,
            size=volume,
            fill_price=open_price,
            exit_price=exit_price,
            order_ticket=ticket,
            last_update=now_iso,
        )
        state.rungs.append(new_rung)
        self.order_mgr.set_exit_tp(symbol, ticket, exit_price)
        log.info(
            f"{symbol}: ADOPTED {source} {side} {volume} lots at {open_price:.5f} "
            f"→ exit at {exit_price:.5f} [{rung_id}]"
        )
        return new_rung

    def _cancel_all_orphaned_orders(self):
        """Cancel ALL pending orders with our magic number on startup.

        After a crash, there may be orphaned orders from a previous grid_id
        that the sync logic won't recognize (it only matches the current
        grid_id prefix). This ensures a clean slate on every startup.
        """
        pending = self.broker.pending_orders()
        if not pending:
            return
        cancelled_by_symbol: dict[str, int] = {}
        for order in pending:
            ticket = order.get("ticket", 0)
            symbol = order.get("symbol", "")
            if ticket:
                self.order_mgr.cancel(ticket, reason="startup_cleanup")
                cancelled_by_symbol[symbol] = cancelled_by_symbol.get(symbol, 0) + 1
        for symbol, count in cancelled_by_symbol.items():
            log.info(
                f"{symbol}: cancelled {count} orphaned orders from previous session"
            )

    def _adopt_orphaned_positions(self):
        """Adopt any open positions from a previous session into the grid.

        On restart after a crash, there may be open positions with our
        magic number that don't belong to any rung. Instead of closing
        them at a loss, we CREATE rungs for them in EXIT state with
        proper take-profit exits. The grid then manages them normally —
        placing exit orders to capture profit.
        """
        for symbol in self._active_symbols:
            state = self.states.get(symbol)
            if state is None:
                continue

            sym_info = self.broker.symbol_info(symbol) or {}
            positions = self.broker.our_positions(symbol)
            if not positions:
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            tick = self.broker.symbol_tick(symbol)
            tick_size = sym_info.get("trade_tick_size") or sym_info.get("point", 0.0)
            mid = (tick["bid"] + tick["ask"]) / 2.0 if tick else 0.0
            spread = (tick["ask"] - tick["bid"]) if tick else 0.0
            if state.spacing > 0:
                spacing = state.spacing
            elif tick_size > 0 and mid > 0:
                spacing, _ = compute_spacing(
                    mid=mid,
                    spread=spread,
                    tick_size=tick_size,
                    sigma=0.0,
                    step_seconds=cfg.GRID_LOOP_SECONDS,
                    horizon_seconds=cfg.VOL_HORIZON_SECONDS,
                    k_sigma=cfg.GRID_SPACING_K_SIGMA,
                    k_cost=cfg.GRID_SPACING_K_COST,
                    min_ticks=cfg.GRID_SPACING_MIN_TICKS,
                    slip_ticks=cfg.SLIPPAGE_BUFFER_TICKS,
                )
            elif tick_size > 0:
                spacing = cfg.GRID_SPACING_MIN_TICKS * tick_size
            else:
                spacing = 0.00030
            adopted = 0

            for pos in positions:
                ticket = pos.get("ticket", 0)
                volume = pos.get("volume", 0.0)
                open_price = pos.get("price_open", 0.0)
                pos_type = pos.get("type", 0)

                if volume <= 0 or open_price <= 0:
                    continue
                side = "BUY" if pos_type == mt5.POSITION_TYPE_BUY else "SELL"
                rung = self._attach_position_to_rung(
                    symbol=symbol,
                    state=state,
                    side=side,
                    open_price=open_price,
                    volume=volume,
                    ticket=ticket,
                    grid_center=state.center,
                    spacing=spacing,
                    now_iso=now_iso,
                    source="startup_adopt",
                )
                if rung:
                    adopted += 1

            if adopted:
                self.alerter.custom(
                    f"<b>ADOPTED</b> {symbol}\n"
                    f"{adopted} orphaned position(s) from previous session\n"
                    f"Exit orders will be placed to capture profit"
                )

    def _pre_weekend_wind_down(self):
        """Pre-weekend protection: cancel entries and close stuck positions.

        For each active symbol:
        1. Cancel ALL entry orders (prevent gap fills on Sunday open)
        2. For each EXIT rung (open position with pending take-profit):
           - If the TP is CLOSE to current price (within threshold) → keep it
             (it will likely fill Monday, no point closing at a loss)
           - If the TP is FAR from current price (beyond threshold) → close
             at market now (take the small loss, avoid weekend gap risk)
        3. Keep exit orders for positions we're keeping

        Cost: ~$6-9 per week in small realized losses on stuck positions.
        Benefit: eliminates weekend gap risk entirely.
        """
        threshold = cfg.WEEKEND_CLOSE_THRESHOLD_SPACINGS

        for symbol in self._active_symbols:
            state = self.states.get(symbol)
            if state is None:
                continue

            sym_info = self.broker.symbol_info(symbol) or {}
            tick_size = sym_info.get("trade_tick_size") or sym_info.get("point", 0.0)
            spacing = state.spacing if state.spacing > 0 else tick_size * 20
            if tick_size <= 0:
                continue

            tick = self.broker.symbol_tick(symbol)
            if tick is None:
                continue
            mid = (tick["bid"] + tick["ask"]) / 2.0

            # Classify EXIT rungs: keep (close to TP) or close (far from TP)
            kept_count = 0
            closed_count = 0
            closed_pnl = 0.0

            for rung in state.rungs:
                if rung.state != "EXIT" or rung.exit_price is None:
                    continue

                # How far is the exit (TP) from current price, in spacings?
                distance = abs(rung.exit_price - mid) / spacing

                if distance <= threshold:
                    # Close to TP — keep the position and exit order
                    kept_count += 1
                else:
                    # Far from TP — close at market to avoid weekend gap risk
                    if rung.size <= 0 or not rung.order_ticket:
                        continue
                    close_side = "SELL" if rung.entry_side == "BUY" else "BUY"
                    success = self.order_mgr.close_position_by_ticket(
                        symbol, rung.order_ticket, rung.size, close_side,
                        reason="weekend_close"
                    )
                    if success:
                        contract_sz = sym_info.get("trade_contract_size", 100000)
                        if rung.entry_side == "BUY":
                            pnl = (mid - (rung.fill_price or mid)) * rung.size * contract_sz
                        else:
                            pnl = ((rung.fill_price or mid) - mid) * rung.size * contract_sz

                        closed_pnl += pnl
                        closed_count += 1

                        # Reset rung to ENTRY
                        rung.state = "ENTRY"
                        rung.fill_price = None
                        rung.exit_price = None
                        rung.order_ticket = None
                        rung.entry_price = state.center + rung.level_index * spacing
                    else:
                        log.warning(
                            f"{symbol}: failed to close {rung.rung_id} for weekend"
                        )

            # Now sync orders: cancel entries, keep exits for remaining positions
            exit_only = desired_orders(
                symbol=symbol,
                grid_id=state.grid_id,
                rungs=state.rungs,
                entry_sizes={},
                allow_entries=False,
            )
            limit_orders = int(self.account_model.get("limit_orders", 0) or 0)
            max_pending = (
                min(cfg.MAX_PENDING_ORDERS_PER_SYMBOL, limit_orders)
                if limit_orders > 0
                else cfg.MAX_PENDING_ORDERS_PER_SYMBOL
            )
            self.order_mgr.sync_orders(
                symbol=symbol,
                desired=exit_only,
                grid_id=state.grid_id,
                tick_size=tick_size,
                requote_ticks=cfg.REQUOTE_THRESHOLD_TICKS,
                max_pending=max_pending,
                current_price=mid,
            )

            log.info(
                f"{symbol}: pre-weekend wind-down complete — "
                f"closed {closed_count} stuck positions (pnl=${closed_pnl:.2f}), "
                f"kept {kept_count} near-TP positions"
            )

    def _init_symbols(self, symbols: list[str]):
        """Initialize tracking for new symbols."""
        for symbol in symbols:
            if not self.broker.select_symbol(symbol):
                log.warning(f"Cannot select {symbol} - skipping")
                continue
            if symbol not in self.states:
                grid_id = uuid.uuid4().hex[:6]
                self.states[symbol] = new_state(symbol, grid_id)
            self.anchors[symbol] = EmaAnchor(cfg.ANCHOR_HALFLIFE_SECONDS)
            self.vol_fast[symbol] = VolEstimator(
                cfg.VOL_EWMA_LAMBDA, reference_dt=cfg.GRID_LOOP_SECONDS
            )
            self.vol_slow[symbol] = VolEstimator(
                min(cfg.VOL_EWMA_LAMBDA + 0.02, 0.995),
                reference_dt=cfg.GRID_LOOP_SECONDS,
            )
            self.price_history[symbol] = deque(maxlen=HISTORY_LEN)
            self.spread_history[symbol] = deque(maxlen=HISTORY_LEN)
            self.trend_filters[symbol] = TrendRegimeFilter(
                trend_thresh=cfg.TREND_SLOPE_Z,
                trend_pause_mult=cfg.TREND_HARD_PAUSE_MULT,
                trend_confirm_bars=cfg.TREND_CONFIRM_BARS,
                range_confirm_bars=cfg.RANGE_CONFIRM_BARS,
            )
            self.last_sync[symbol] = 0.0

    # =====================================================================
    #  MAIN LOOP
    # =====================================================================

    def _main_loop(self):
        global _shutdown_requested
        _market_was_closed = False

        while not _shutdown_requested:
            loop_start = time.time()
            try:
                # ── Market hours check ───────────────────────────────────
                if not _is_market_open():
                    if not _market_was_closed:
                        log.info("Market closed — running pre-weekend wind-down")
                        self._pre_weekend_wind_down()
                        save_state(self.states)  # W5: persist after wind-down
                        _market_was_closed = True
                    # Sleep longer during market close (no point checking every 2s)
                    time.sleep(30)
                    continue

                self.broker.ensure_connected()

                # ── Just reopened after market close ─────────────────────
                if _market_was_closed:
                    log.info("Market reopened — reinitializing estimators")
                    # Reset anchor/vol estimators (stale from Friday)
                    for symbol in self._active_symbols:
                        self.anchors[symbol] = EmaAnchor(cfg.ANCHOR_HALFLIFE_SECONDS)
                        self.vol_fast[symbol] = VolEstimator(
                            cfg.VOL_EWMA_LAMBDA, reference_dt=cfg.GRID_LOOP_SECONDS
                        )
                        self.vol_slow[symbol] = VolEstimator(
                            min(cfg.VOL_EWMA_LAMBDA + 0.02, 0.995),
                            reference_dt=cfg.GRID_LOOP_SECONDS,
                        )
                        self.price_history[symbol] = deque(maxlen=HISTORY_LEN)
                        self.spread_history[symbol] = deque(maxlen=HISTORY_LEN)
                        self.trend_filters[symbol] = TrendRegimeFilter(
                            trend_thresh=cfg.TREND_SLOPE_Z,
                            trend_pause_mult=cfg.TREND_HARD_PAUSE_MULT,
                            trend_confirm_bars=cfg.TREND_CONFIRM_BARS,
                            range_confirm_bars=cfg.RANGE_CONFIRM_BARS,
                        )
                    _market_was_closed = False

                # ── Pre-weekend wind down (Friday after 21:00 UTC) ───────
                pre_weekend = _is_pre_weekend_wind_down()

                # ── Periodic re-scan ─────────────────────────────────────
                if cfg.AUTO_SCAN_ENABLED and not pre_weekend:
                    if (time.time() - self._last_scan_ts) >= cfg.SCAN_INTERVAL_SECONDS:
                        self._run_scan()

                # ── Process active symbols ───────────────────────────────
                for symbol in self._active_symbols:
                    if _shutdown_requested:
                        break
                    self._process_symbol(symbol, block_entries=pre_weekend)

                # ── Process retiring symbols (exits only) ────────────────
                for symbol in list(self._retiring_symbols):
                    if _shutdown_requested:
                        break
                    self._process_retiring_symbol(symbol)

                save_state(self.states)
                self.risk_mgr.clear_errors()

                # ── Periodic status log (every 5 minutes) ────────────────
                now_status = time.time()
                if now_status - self._last_status_log_ts >= self._status_log_interval:
                    for symbol in self._active_symbols:
                        state = self.states.get(symbol)
                        if state is None:
                            continue
                        tick = self.broker.symbol_tick(symbol)
                        mid = (tick["bid"] + tick["ask"]) / 2.0 if tick else 0.0
                        exit_count = sum(1 for r in state.rungs if r.state == "EXIT")
                        entry_count = sum(1 for r in state.rungs if r.state == "ENTRY")
                        equity = self.broker.account_equity()
                        log.info(
                            f"{symbol}: mid={mid:.5f} center={state.center:.5f} "
                            f"spacing={state.spacing:.5f} "
                            f"entries={entry_count} exits={exit_count} "
                            f"inv={state.inventory_lots:+.3f} "
                            f"fills={self._fill_count} pnl=${self._session_pnl:.2f} "
                            f"equity=${equity:.2f}"
                        )
                    self._last_status_log_ts = now_status

            except ConnectionError:
                log.error("MT5 connection lost and could not be restored")
                self.risk_mgr.record_error()
                try:
                    bal = self.broker.account_balance()
                    self.alerter.safety_event("CONNECTION_LOST", "MT5 unreachable", bal)
                except Exception:
                    pass
            except Exception as exc:
                log.error(f"Grid loop error: {exc}", exc_info=True)
                self.risk_mgr.record_error()
                try:
                    bal = self.broker.account_balance()
                    self.alerter.safety_event("LOOP_ERROR", str(exc), bal)
                except Exception:
                    pass

            elapsed = time.time() - loop_start
            sleep_for = max(0.1, cfg.GRID_LOOP_SECONDS - elapsed)
            time.sleep(sleep_for)

        self._shutdown()

    # =====================================================================
    #  PER-SYMBOL PROCESSING
    # =====================================================================

    def _process_symbol(self, symbol: str, block_entries: bool = False):
        state = self.states.get(symbol)
        if state is None:
            return

        tick = self.broker.symbol_tick(symbol)
        if tick is None:
            return
        bid = tick.get("bid", 0.0)
        ask = tick.get("ask", 0.0)
        if bid <= 0 or ask <= 0:
            return
        mid = (bid + ask) / 2.0
        spread = ask - bid
        sym_info = self.broker.symbol_info(symbol) or {}
        tick_size = sym_info.get("trade_tick_size") or sym_info.get("point", 0.0)
        if tick_size <= 0:
            return

        now_ts = time.time()

        # Update histories
        self.price_history[symbol].append(mid)
        self.spread_history[symbol].append(spread / tick_size)

        prices = self.price_history[symbol]
        spreads = self.spread_history[symbol]

        # Update anchor + volatility
        anchor = self.anchors[symbol].update(mid, now_ts)
        sigma_fast = self.vol_fast[symbol].update(mid, now_ts)
        sigma_slow = self.vol_slow[symbol].update(mid, now_ts)
        vol_ratio = sigma_fast / max(sigma_slow, 1e-9)

        spacing, cost_floor = compute_spacing(
            mid=mid,
            spread=spread,
            tick_size=tick_size,
            sigma=sigma_fast,
            step_seconds=cfg.GRID_LOOP_SECONDS,
            horizon_seconds=cfg.VOL_HORIZON_SECONDS,
            k_sigma=cfg.GRID_SPACING_K_SIGMA,
            k_cost=cfg.GRID_SPACING_K_COST,
            min_ticks=cfg.GRID_SPACING_MIN_TICKS,
            slip_ticks=cfg.SLIPPAGE_BUFFER_TICKS,
        )
        if spacing <= 0:
            return

        # Inventory
        positions = self.broker.our_positions(symbol)
        inventory_lots = 0.0
        for pos in positions:
            ptype = pos.get("type", mt5.POSITION_TYPE_BUY)
            sign = 1 if ptype == mt5.POSITION_TYPE_BUY else -1
            inventory_lots += sign * pos.get("volume", 0.0)
        state.inventory_lots = inventory_lots

        inv_ratio = 0.0
        if cfg.MAX_INVENTORY_LOTS > 0:
            inv_ratio = max(-1.0, min(1.0, inventory_lots / cfg.MAX_INVENTORY_LOTS))

        center = anchor - cfg.CENTER_SKEW_K * inv_ratio * spacing
        grid_center = state.center if state.center else center
        grid_spacing = state.spacing if state.spacing else spacing

        # Regime
        trend_z = compute_trend_z(list(prices))
        spread_ratio = (
            (spread / tick_size) / max(1.0, median(spreads))
            if len(spreads) > 1
            else 1.0
        )
        regime = classify_regime(
            trend_z=trend_z,
            vol_ratio=vol_ratio,
            spread_ratio=spread_ratio,
            trend_thresh=cfg.TREND_SLOPE_Z,
            vol_thresh=cfg.VOL_SHOCK_RATIO,
            spread_thresh=cfg.SPREAD_PAUSE_MULT,
            trend_pause_mult=cfg.TREND_HARD_PAUSE_MULT,
        )

        trend_filter = self.trend_filters.get(symbol)
        if trend_filter is None:
            trend_filter = TrendRegimeFilter(
                trend_thresh=cfg.TREND_SLOPE_Z,
                trend_pause_mult=cfg.TREND_HARD_PAUSE_MULT,
                trend_confirm_bars=cfg.TREND_CONFIRM_BARS,
                range_confirm_bars=cfg.RANGE_CONFIRM_BARS,
            )
            self.trend_filters[symbol] = trend_filter
        trend_state = trend_filter.update(
            prices=list(prices),
            vol_ratio=vol_ratio,
            spread_ratio=spread_ratio,
            spread_thresh=cfg.SPREAD_PAUSE_MULT,
            vol_thresh=cfg.VOL_SHOCK_RATIO,
        )
        if trend_state.regime == "TREND" and regime.mode != "PAUSED":
            regime.mode = "PAUSED"
            regime.reason = "trend_model"
        elif trend_state.regime == "RANGE" and regime.mode == "PAUSED" and regime.reason == "trend_model":
            regime.mode = "CAUTION" if abs(trend_z) >= cfg.TREND_SLOPE_Z else "ACTIVE"
            regime.reason = "trend" if regime.mode == "CAUTION" else ""

        # Risk checks
        # H5: Guard against missing contract_size — default 1.0 would make
        # leverage checks 100,000x too lenient for FX.
        contract_size = sym_info.get("trade_contract_size", 0.0)
        if contract_size <= 0:
            log.error(f"{symbol}: trade_contract_size missing or zero — skipping")
            return
        notional = abs(inventory_lots) * contract_size * mid
        risk_status = self.risk_mgr.check_limits(
            symbol=symbol,
            inventory_lots=inventory_lots,
            notional=notional,
            price=mid,
        )
        if risk_status.halted:
            if state.pause_reason != risk_status.reason:
                bal = self.broker.account_balance()
                self.alerter.safety_event(
                    "RISK HALT",
                    f"{symbol}: {risk_status.reason}",
                    bal,
                )
            state.paused = True
            state.pause_reason = risk_status.reason
        elif regime.mode == "PAUSED":
            state.paused = True
            state.pause_reason = f"regime:{regime.reason}"
        else:
            state.paused = False
            state.pause_reason = ""
            self.order_mgr.clear_unwind_flag(symbol)

        # Reset logic
        now = time.time()
        reset_needed = False
        if state.center == 0.0 or state.spacing == 0.0 or not state.rungs:
            reset_needed = True
        elif abs(center - state.center) >= cfg.GRID_RESET_K * spacing:
            if (now - state.last_reset_ts) >= cfg.GRID_RESET_MIN_SECONDS:
                reset_needed = True
        if not reset_needed:
            standard_rungs = [r for r in state.rungs if r.rung_id.startswith("L")]
            max_level = max((abs(r.level_index) for r in standard_rungs), default=0)
            if max_level != cfg.GRID_LEVELS:
                reset_needed = True

        if reset_needed:
            state.center = center
            state.anchor = anchor
            state.spacing = spacing
            state.levels = cfg.GRID_LEVELS
            state.last_reset_ts = now
            # Check if standard grid rungs exist and match configured levels
            standard_rungs = [r for r in state.rungs if r.rung_id.startswith("L")]
            has_standard_rungs = len(standard_rungs) > 0
            max_level = max((abs(r.level_index) for r in standard_rungs), default=0)
            levels_match = max_level == cfg.GRID_LEVELS

            if not has_standard_rungs or not levels_match:
                # Build standard grid, preserving any adopted rungs (A0, A1, etc.)
                adopted_rungs = [r for r in state.rungs if not r.rung_id.startswith("L")]
                state.rungs = build_rungs(symbol, center, spacing, cfg.GRID_LEVELS)
                state.rungs.extend(adopted_rungs)
            else:
                for rung in state.rungs:
                    if rung.state == "ENTRY":
                        rung.entry_price = center + rung.level_index * spacing
            grid_center = state.center
            grid_spacing = state.spacing
            log.info(f"{symbol}: grid reset center={center:.5f} spacing={spacing:.5f}")
        else:
            state.anchor = anchor

        # Adopt any open positions that are not yet attached to a rung
        now_iso = datetime.now(timezone.utc).isoformat()
        for pos in positions:
            ticket = pos.get("ticket", 0)
            volume = pos.get("volume", 0.0)
            open_price = pos.get("price_open", 0.0)
            pos_type = pos.get("type", mt5.POSITION_TYPE_BUY)
            if volume <= 0 or open_price <= 0:
                continue
            side = "BUY" if pos_type == mt5.POSITION_TYPE_BUY else "SELL"
            self._attach_position_to_rung(
                symbol=symbol,
                state=state,
                side=side,
                open_price=open_price,
                volume=volume,
                ticket=ticket,
                grid_center=grid_center,
                spacing=grid_spacing,
                now_iso=now_iso,
                source="runtime_adopt",
            )

        entry_cooldown_active = now < self._entry_pause_until.get(symbol, 0.0)

        # Edge check
        edge_ok = grid_spacing - cost_floor >= cfg.EDGE_MIN_TICKS * tick_size
        allow_entries = (
            not state.paused
            and edge_ok
            and not block_entries
            and not entry_cooldown_active
        )

        # Size adjustments
        size_multiplier = risk_status.risk_multiplier
        if regime.mode == "CAUTION":
            size_multiplier *= 0.5

        entry_sizes: dict[str, float] = {}
        for rung in state.rungs:
            if rung.state != "ENTRY":
                continue
            side = rung.entry_side
            if inv_ratio >= 0.95 and side == "BUY":
                entry_sizes[rung.rung_id] = 0.0
                continue
            if inv_ratio <= -0.95 and side == "SELL":
                entry_sizes[rung.rung_id] = 0.0
                continue
            base_size = cfg.BASE_ORDER_SIZE_LOTS * size_multiplier
            size = size_for_rung(
                side=side,
                level_index=rung.level_index,
                base_size=base_size,
                inv_ratio=inv_ratio,
                gamma=cfg.INVENTORY_SKEW_GAMMA,
                eta=cfg.SIZE_TAPER_ETA,
            )
            entry_sizes[rung.rung_id] = size

        # Fills — two types: ENTRY fills (new positions) and EXIT fills (TP closes)
        fills = self.order_mgr.detect_fills(
            symbol, state.last_deal_time, state.grid_id, state.last_deal_id
        )
        if fills:
            equity = self.broker.account_equity()
            latest_time = state.last_deal_time
            latest_deal_id = state.last_deal_id
            for fill in fills:
                if fill.deal_entry == 0:
                    # ── ENTRY FILL: limit order filled, position opened ──
                    rung = None
                    if fill.rung_id:
                        rung = next(
                            (r for r in state.rungs if r.rung_id == fill.rung_id),
                            None,
                        )
                    if not rung and fill.position_ticket:
                        rung = next(
                            (r for r in state.rungs
                             if r.order_ticket == fill.position_ticket),
                            None,
                        )
                    if rung and rung.state == "EXIT" and fill.position_ticket:
                        if rung.order_ticket == fill.position_ticket:
                            # Duplicate deal for same position — ignore
                            latest_time = max(latest_time, fill.time_iso)
                            latest_deal_id = max(latest_deal_id, fill.deal_id)
                            continue

                    if not rung or rung.state == "EXIT":
                        rung = self._attach_position_to_rung(
                            symbol=symbol,
                            state=state,
                            side=fill.side,
                            open_price=fill.price,
                            volume=fill.volume,
                            ticket=fill.position_ticket,
                            grid_center=grid_center,
                            spacing=grid_spacing,
                            now_iso=fill.time_iso,
                            source="fill_adopt",
                        )

                    if not rung:
                        latest_time = max(latest_time, fill.time_iso)
                        latest_deal_id = max(latest_deal_id, fill.deal_id)
                        continue

                    if rung.state == "ENTRY":
                        update_rung_on_fill(rung, fill.price, grid_spacing, fill.time_iso)

                    if rung.state == "EXIT":
                        rung.size = fill.volume
                        # Find the position ticket for this fill
                        pos_ticket = fill.position_ticket or rung.order_ticket
                        if not pos_ticket:
                            # Fallback: find from broker positions
                            for p in positions:
                                if abs(p.get("price_open", 0) - fill.price) < grid_spacing * 0.3:
                                    if abs(p.get("volume", 0) - fill.volume) < 0.015:
                                        pos_ticket = p.get("ticket", 0)
                                        break
                        rung.order_ticket = pos_ticket
                        # Set T/P on the position
                        if pos_ticket and rung.exit_price:
                            self.order_mgr.set_exit_tp(
                                symbol, pos_ticket, rung.exit_price
                            )

                    latest_time = max(latest_time, fill.time_iso)
                    latest_deal_id = max(latest_deal_id, fill.deal_id)
                    self.order_mgr.journal.log_fill(
                        fill, equity=equity, inventory_lots=inventory_lots
                    )
                    self._fill_count += 1
                    log.info(
                        f"{symbol}: ENTRY FILL {fill.side} {fill.volume} lots "
                        f"at {fill.price:.5f} [{rung.rung_id}] "
                        f"pos=#{rung.order_ticket} exit_tp={rung.exit_price} "
                        f"inv={inventory_lots:+.3f}"
                    )
                    self.alerter.custom(
                        f"<b>ENTRY</b> {symbol}\n"
                        f"{fill.side} {fill.volume} lots at {fill.price:.5f}\n"
                        f"TP set at {rung.exit_price:.5f}\n"
                        f"Equity: ${equity:,.2f}"
                    )

                elif fill.deal_entry == 1:
                    # ── EXIT FILL: position closed at TP ──
                    # Find the rung by rung_id (from comment) or position_ticket
                    rung = None
                    if fill.rung_id:
                        rung = next(
                            (r for r in state.rungs if r.rung_id == fill.rung_id),
                            None,
                        )
                    if not rung and fill.position_ticket:
                        rung = next(
                            (r for r in state.rungs
                             if r.state == "EXIT"
                             and r.order_ticket == fill.position_ticket),
                            None,
                        )
                    if not rung:
                        # Try matching by price
                        rung = next(
                            (r for r in state.rungs
                             if r.state == "EXIT"
                             and r.exit_price
                             and abs(r.exit_price - fill.price) < grid_spacing * 0.5),
                            None,
                        )
                    if not rung:
                        continue

                    fill_vol = fill.volume if fill.volume > 0 else rung.size
                    # Calculate profit for the filled volume
                    if rung.entry_side == "BUY":
                        pnl = (fill.price - (rung.fill_price or 0)) * fill_vol * contract_size
                    else:
                        pnl = ((rung.fill_price or 0) - fill.price) * fill_vol * contract_size
                    self._session_pnl += pnl

                    # Partial close: reduce size, keep EXIT state
                    if fill_vol < max(0.0, rung.size - 1e-4):
                        rung.size = max(0.0, rung.size - fill_vol)
                        rung.last_update = fill.time_iso
                        log.info(
                            f"{symbol}: PARTIAL EXIT {fill.side} {fill_vol} lots "
                            f"at {fill.price:.5f} [{rung.rung_id}] "
                            f"remaining={rung.size:.3f} pnl=${pnl:.2f}"
                        )
                    else:
                        # Reset rung to ENTRY
                        rung.state = "ENTRY"
                        rung.entry_price = grid_center + rung.level_index * grid_spacing
                        rung.fill_price = None
                        rung.exit_price = None
                        rung.order_ticket = None
                        rung.last_update = fill.time_iso

                    latest_time = max(latest_time, fill.time_iso)
                    latest_deal_id = max(latest_deal_id, fill.deal_id)
                    self.order_mgr.journal.log_fill(
                        fill, equity=equity, inventory_lots=inventory_lots
                    )
                    self._fill_count += 1
                    log.info(
                        f"{symbol}: EXIT FILL (TP) {fill.side} {fill.volume} lots "
                        f"at {fill.price:.5f} [{rung.rung_id}] "
                        f"pnl=${pnl:.2f} inv={inventory_lots:+.3f}"
                    )
                    self.alerter.custom(
                        f"<b>PROFIT</b> {symbol}\n"
                        f"Closed at {fill.price:.5f}\n"
                        f"P/L: ${pnl:+.2f}\n"
                        f"Equity: ${equity:,.2f}"
                    )

            state.last_deal_time = latest_time
            state.last_deal_id = latest_deal_id

        # Ensure TP is set for all EXIT rungs (hedging safety net)
        positions_by_ticket = {
            p.get("ticket", 0): p for p in positions if p.get("ticket", 0)
        }
        for rung in state.rungs:
            if rung.state != "EXIT" or not rung.order_ticket:
                continue
            pos = positions_by_ticket.get(rung.order_ticket)
            if not pos:
                continue
            if rung.exit_price is None and rung.fill_price is not None:
                if rung.entry_side == "BUY":
                    rung.exit_price = rung.fill_price + grid_spacing
                else:
                    rung.exit_price = rung.fill_price - grid_spacing
            if rung.exit_price is None:
                continue
            current_tp = pos.get("tp", 0.0)
            if current_tp <= 0 or abs(current_tp - rung.exit_price) > tick_size * 0.5:
                self.order_mgr.set_exit_tp(
                    symbol, rung.order_ticket, rung.exit_price
                )

        if cfg.RUNG_STOP_LOSS_SPACINGS > 0:
            now_iso = datetime.now(timezone.utc).isoformat()
            stop_hits = 0
            stop_pnl = 0.0
            for rung in state.rungs:
                if (
                    rung.state != "EXIT"
                    or rung.fill_price is None
                    or rung.order_ticket is None
                    or rung.size <= 0
                ):
                    continue
                if rung.order_ticket not in positions_by_ticket:
                    continue

                if rung.entry_side == "BUY":
                    stop_price = rung.fill_price - cfg.RUNG_STOP_LOSS_SPACINGS * grid_spacing
                    stop_hit = bid <= stop_price
                    close_side = "SELL"
                    pnl = (stop_price - rung.fill_price) * rung.size * contract_size
                else:
                    stop_price = rung.fill_price + cfg.RUNG_STOP_LOSS_SPACINGS * grid_spacing
                    stop_hit = ask >= stop_price
                    close_side = "BUY"
                    pnl = (rung.fill_price - stop_price) * rung.size * contract_size

                if not stop_hit:
                    continue

                closed = self.order_mgr.close_position_by_ticket(
                    symbol,
                    rung.order_ticket,
                    rung.size,
                    close_side,
                    reason="rung_stop",
                )
                if not closed:
                    continue

                stop_hits += 1
                stop_pnl += pnl
                self._session_pnl += pnl
                log.warning(
                    f"{symbol}: STOP LOSS {close_side} {rung.size} lots at "
                    f"{stop_price:.5f} [{rung.rung_id}] pnl=${pnl:.2f}"
                )

                rung.state = "ENTRY"
                rung.entry_price = grid_center + rung.level_index * grid_spacing
                rung.fill_price = None
                rung.exit_price = None
                rung.order_ticket = None
                rung.last_update = now_iso

            if stop_hits > 0:
                cooldown_until = time.time() + cfg.STOP_LOSS_COOLDOWN_SECONDS
                self._entry_pause_until[symbol] = cooldown_until
                state.paused = True
                state.pause_reason = "stop_loss_cooldown"
                self.alerter.custom(
                    f"<b>STOP LOSS</b> {symbol}\n"
                    f"Closed {stop_hits} rung(s), P/L: ${stop_pnl:+.2f}\n"
                    f"Entries paused for {cfg.STOP_LOSS_COOLDOWN_SECONDS}s"
                )

        # If a position vanished but no deal was captured, infer a TP close.
        missing_tickets = {
            r.order_ticket
            for r in state.rungs
            if r.state == "EXIT" and r.order_ticket
        } - set(positions_by_ticket.keys())
        if missing_tickets:
            equity = self.broker.account_equity()
            now_iso = datetime.now(timezone.utc).isoformat()
            inferred_count = 0
            for rung in state.rungs:
                if rung.state != "EXIT" or not rung.order_ticket:
                    continue
                if rung.order_ticket not in missing_tickets:
                    continue
                fill_price = rung.exit_price or mid
                fill_vol = rung.size
                if fill_vol <= 0:
                    continue
                if rung.entry_side == "BUY":
                    pnl = (fill_price - (rung.fill_price or 0)) * fill_vol * contract_size
                    close_side = "SELL"
                else:
                    pnl = ((rung.fill_price or 0) - fill_price) * fill_vol * contract_size
                    close_side = "BUY"
                self._session_pnl += pnl
                fill = FillEvent(
                    symbol=symbol,
                    side=close_side,
                    price=fill_price,
                    volume=fill_vol,
                    order_ticket=0,
                    comment="inferred_close",
                    time_iso=now_iso,
                    rung_id=rung.rung_id,
                    state="EXIT",
                    deal_entry=1,
                    position_ticket=rung.order_ticket or 0,
                    deal_id=0,
                )
                self.order_mgr.journal.log_fill(
                    fill, equity=equity, inventory_lots=inventory_lots
                )
                self._fill_count += 1
                log.info(
                    f"{symbol}: EXIT FILL (inferred) {close_side} {fill_vol} lots "
                    f"at {fill_price:.5f} [{rung.rung_id}] pnl=${pnl:.2f}"
                )
                self.alerter.custom(
                    f"<b>PROFIT</b> {symbol}\n"
                    f"Closed (inferred) at {fill_price:.5f}\n"
                    f"P/L: ${pnl:+.2f}\n"
                    f"Equity: ${equity:,.2f}"
                )
                rung.state = "ENTRY"
                rung.entry_price = grid_center + rung.level_index * grid_spacing
                rung.fill_price = None
                rung.exit_price = None
                rung.order_ticket = None
                rung.last_update = now_iso
                inferred_count += 1
            if inferred_count > 0 and now_iso > state.last_deal_time:
                state.last_deal_time = now_iso

        # Desired orders (C1: pass vol_min to skip sub-minimum orders)
        vol_min = sym_info.get("volume_min", 0.01)
        orders = desired_orders(
            symbol=symbol,
            grid_id=state.grid_id,
            rungs=state.rungs,
            entry_sizes=entry_sizes,
            allow_entries=allow_entries,
            vol_min=vol_min,
        )

        # Unwind if halted — close all positions by ticket on hedging account
        if risk_status.halted:
            limit_orders = int(self.account_model.get("limit_orders", 0) or 0)
            max_pending = (
                min(cfg.MAX_PENDING_ORDERS_PER_SYMBOL, limit_orders)
                if limit_orders > 0
                else cfg.MAX_PENDING_ORDERS_PER_SYMBOL
            )
            # Cancel all pending entry orders
            self.order_mgr.sync_orders(
                symbol=symbol,
                desired=[],
                grid_id=state.grid_id,
                tick_size=tick_size,
                requote_ticks=cfg.REQUOTE_THRESHOLD_TICKS,
                max_pending=max_pending,
                current_price=mid,
            )
            # Close each open position by its ticket
            for rung in state.rungs:
                if rung.state == "EXIT" and rung.order_ticket and rung.size > 0:
                    close_side = "SELL" if rung.entry_side == "BUY" else "BUY"
                    self.order_mgr.close_position_by_ticket(
                        symbol, rung.order_ticket, rung.size, close_side,
                        reason="risk_halt"
                    )
                    rung.state = "ENTRY"
                    rung.fill_price = None
                    rung.exit_price = None
                    rung.order_ticket = None
            return

        # Sync orders at cadence
        force_sync = reset_needed or bool(fills)
        if now - self.last_sync[symbol] >= cfg.ORDER_REFRESH_SECONDS or force_sync:
            limit_orders = int(self.account_model.get("limit_orders", 0) or 0)
            max_pending = (
                min(cfg.MAX_PENDING_ORDERS_PER_SYMBOL, limit_orders)
                if limit_orders > 0
                else cfg.MAX_PENDING_ORDERS_PER_SYMBOL
            )
            self.order_mgr.sync_orders(
                symbol=symbol,
                desired=orders,
                grid_id=state.grid_id,
                tick_size=tick_size,
                requote_ticks=cfg.REQUOTE_THRESHOLD_TICKS,
                max_pending=max_pending,
                current_price=mid,
            )
            self.last_sync[symbol] = now

        state.last_update_ts = now

    # =====================================================================
    #  SHUTDOWN
    # =====================================================================

    def _shutdown(self):
        log.info("Shutting down Grid Trading System ...")
        try:
            self.alerter.bot_status("STOPPED")
        except Exception:
            pass
        try:
            self.broker.disconnect()
        except Exception:
            pass
        log.info("Grid Trading System stopped.")
