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
from execution.order_manager import OrderManager
from grid.anchor import EmaAnchor
from grid.orders import build_rungs, desired_orders, update_rung_on_fill
from grid.regime import classify_regime, compute_trend_z
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
        self.last_sync: dict[str, float] = {}
        self.account_model: dict[str, object] = {}

        # Scanner state
        self._active_symbols: list[str] = []
        self._retiring_symbols: list[str] = []  # symbols winding down (exits only)
        self._last_scan_ts: float = 0.0
        self._scan_results: list[SymbolScore] = []

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

        self._init_symbols(self._active_symbols)
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
        fills = self.order_mgr.detect_fills(symbol, state.last_deal_time)
        if fills:
            equity = self.broker.account_equity()
            latest_time = state.last_deal_time
            for fill in fills:
                rung = next(
                    (r for r in state.rungs if r.rung_id == fill.rung_id), None
                )
                if not rung:
                    continue
                update_rung_on_fill(rung, fill.price, grid_spacing, fill.time_iso)
                latest_time = max(latest_time, fill.time_iso)
                self.order_mgr.journal.log_fill(
                    fill, equity=equity, inventory_lots=state.inventory_lots
                )
                log.info(
                    f"{symbol} [retiring]: exit filled {fill.rung_id} "
                    f"at {fill.price}"
                )
            state.last_deal_time = latest_time

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
                    if rung.size <= 0:
                        continue  # W1: skip if size is zero (bad state)
                    side = "SELL" if rung.entry_side == "BUY" else "BUY"
                    result = self.order_mgr.place_market(
                        symbol, side, rung.size, "weekend_close"
                    )
                    if result and result.get("retcode") in (
                        mt5.TRADE_RETCODE_DONE, 10010
                    ):
                        # Calculate realized loss
                        if rung.entry_side == "BUY":
                            pnl = (mid - (rung.fill_price or mid)) * rung.size * 100000
                        else:
                            pnl = ((rung.fill_price or mid) - mid) * rung.size * 100000

                        closed_pnl += pnl
                        closed_count += 1
                        self.order_mgr.journal.log_safety(
                            "WEEKEND_CLOSE",
                            f"{symbol}: closed {rung.rung_id} {rung.entry_side} "
                            f"{rung.size} lots, distance={distance:.1f} spacings, "
                            f"pnl=${pnl:.2f}"
                        )

                        # Reset rung to ENTRY (position is now closed)
                        rung.state = "ENTRY"
                        rung.fill_price = None
                        rung.exit_price = None
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
        inventory_lots = 0.0
        for pos in self.broker.our_positions(symbol):
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
        )

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

        if reset_needed:
            state.center = center
            state.anchor = anchor
            state.spacing = spacing
            state.levels = cfg.GRID_LEVELS
            state.last_reset_ts = now
            if not state.rungs:
                state.rungs = build_rungs(symbol, center, spacing, cfg.GRID_LEVELS)
            else:
                for rung in state.rungs:
                    if rung.state == "ENTRY":
                        rung.entry_price = center + rung.level_index * spacing
            grid_center = state.center
            grid_spacing = state.spacing
            log.info(f"{symbol}: grid reset center={center:.5f} spacing={spacing:.5f}")
        else:
            state.anchor = anchor

        # Edge check
        edge_ok = grid_spacing - cost_floor >= cfg.EDGE_MIN_TICKS * tick_size
        allow_entries = not state.paused and edge_ok and not block_entries

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

        # Fills
        fills = self.order_mgr.detect_fills(symbol, state.last_deal_time)
        if fills:
            equity = self.broker.account_equity()
            latest_time = state.last_deal_time
            for fill in fills:
                rung = next(
                    (r for r in state.rungs if r.rung_id == fill.rung_id), None
                )
                if not rung:
                    continue
                update_rung_on_fill(rung, fill.price, grid_spacing, fill.time_iso)
                if rung.state == "EXIT":
                    rung.size = fill.volume
                if rung.state == "ENTRY":
                    rung.entry_price = grid_center + rung.level_index * grid_spacing
                latest_time = max(latest_time, fill.time_iso)
                self.order_mgr.journal.log_fill(
                    fill, equity=equity, inventory_lots=inventory_lots
                )
            state.last_deal_time = latest_time

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

        # Unwind if halted
        if risk_status.halted:
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
                current_price=mid,
            )
            if abs(inventory_lots) > 0:
                self.order_mgr.unwind_position(symbol, inventory_lots)
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
