"""
===============================================================================
  WOLF TRADING SYSTEM — Master Orchestrator
===============================================================================
  The main loop that runs the entire system:
    1. Connect to MT5
    2. Discover tradeable universe
    3. Continuously scan for opportunities
    4. Execute high-confidence trades
    5. Monitor open positions
    6. Manage risk
    7. Send alerts via Telegram
===============================================================================

  Usage:
    conda activate tradebot
    python main.py                 # Run the full system
    python main.py --scan-only     # Just scan, don't trade
    python main.py --status        # Show account status and exit
===============================================================================
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timezone

import config as cfg
from core.mt5_connector import MT5Connector
from core.market_scanner import MarketScanner
from core.signals import TradeSignal
from core import ai_analyst
from core import chart_analyst
from core.sniper.pipeline import SniperPipeline
from execution.trade_executor import TradeExecutor
from execution.position_monitor import PositionMonitor
from risk.risk_manager import RiskManager
from alerts.telegram import TelegramAlerter
from utils.logger import setup_logging, get_logger
from utils import market_hours

log = get_logger("main")

# ═════════════════════════════════════════════════════════════════════════════
#  GRACEFUL SHUTDOWN
# ═════════════════════════════════════════════════════════════════════════════

_shutdown_requested = False


def _signal_handler(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    log.info("Shutdown signal received — finishing current cycle …")


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ═════════════════════════════════════════════════════════════════════════════
#  WOLF ENGINE
# ═════════════════════════════════════════════════════════════════════════════

class WolfEngine:
    """The main trading engine that orchestrates all components."""

    def __init__(self, scan_only: bool = False):
        self.scan_only = scan_only

        # ── Initialise components ────────────────────────────────────────
        self.mt5 = MT5Connector()
        self.alerter = TelegramAlerter(
            bot_token=cfg.TELEGRAM_BOT_TOKEN,
            chat_id=cfg.TELEGRAM_CHAT_ID,
        )
        self.risk_mgr = RiskManager(self.mt5)
        self.scanner = MarketScanner(self.mt5)
        self.sniper = SniperPipeline(self.mt5) if cfg.SNIPER_MODE else None
        self.executor = TradeExecutor(self.mt5, self.risk_mgr, self.alerter)
        self.pos_monitor = PositionMonitor(
            self.mt5, self.executor, self.risk_mgr, self.alerter
        )

        self._last_daily_summary: int = -1
        self._last_universe_refresh: float = 0
        self._cycle_count: int = 0
        self._last_daily_reset_day: int = -1
        self._last_weekly_reset_week: int = -1

    def start(self):
        """Connect and begin the main loop."""
        log.info("=" * 70)
        log.info("  WOLF TRADING SYSTEM — Starting")
        log.info("=" * 70)

        # ── Connect to MT5 ───────────────────────────────────────────────
        if not self.mt5.connect():
            log.error("Failed to connect to MT5 — exiting")
            sys.exit(1)

        # ── Account info ─────────────────────────────────────────────────
        acc = self.mt5.account_info()
        log.info(
            f"Account: {acc.get('login')} | "
            f"Balance: ${acc.get('balance', 0):,.2f} | "
            f"Equity: ${acc.get('equity', 0):,.2f} | "
            f"Leverage: 1:{acc.get('leverage', 0)} | "
            f"Trading Capital: ${cfg.TRADING_CAPITAL:,.2f}"
        )

        # Snapshot starting balance for risk manager (stable base for limits)
        starting_balance = acc.get("balance", cfg.TRADING_CAPITAL)
        if self.risk_mgr._day_start_balance <= 0:
            self.risk_mgr._day_start_balance = starting_balance
        if self.risk_mgr._week_start_balance <= 0:
            self.risk_mgr._week_start_balance = starting_balance
        # Update peak equity from live equity at startup
        equity = acc.get("equity", starting_balance)
        self.risk_mgr.update_peak_equity(equity)

        mode = "SCAN ONLY" if self.scan_only else "LIVE TRADING"
        log.info(f"Mode: {mode}")
        log.info(f"Risk per trade: {cfg.MAX_RISK_PER_TRADE_PCT}%")
        log.info(f"Min confidence: {cfg.CONFIDENCE_THRESHOLD}")
        log.info(f"Max positions: {cfg.MAX_CONCURRENT_POSITIONS}")
        log.info(f"Magic number: {cfg.MAGIC_NUMBER}")

        self.alerter.bot_status(
            "STARTED",
            f"Mode: {mode}\n"
            f"Capital: ${cfg.TRADING_CAPITAL:,.2f}\n"
            f"Balance: ${acc.get('balance', 0):,.2f}\n"
            f"Risk/trade: {cfg.MAX_RISK_PER_TRADE_PCT}%",
        )

        # ── Discover universe ────────────────────────────────────────────
        if cfg.SNIPER_MODE and self.sniper:
            self.sniper.refresh_universe()
        else:
            self.scanner.refresh_universe()

        # ── Main loop ────────────────────────────────────────────────────
        try:
            self._main_loop()
        except Exception as e:
            log.error(f"Fatal error: {e}", exc_info=True)
            self.alerter.safety_event(
                "FATAL ERROR", str(e), self.mt5.account_balance()
            )
        finally:
            self._shutdown()

    def _main_loop(self):
        """The beating heart of the system."""
        global _shutdown_requested

        if cfg.SNIPER_MODE:
            self._main_loop_sniper()
            return

        while not _shutdown_requested:
            try:
                self._cycle_count += 1
                cycle_start = time.time()

                # ── Ensure connection ────────────────────────────────────
                self.mt5.ensure_connected()

                # ── Refresh universe every 30 minutes ────────────────────
                if time.time() - self._last_universe_refresh > 1800:
                    self.scanner.refresh_universe()
                    self._last_universe_refresh = time.time()

                # ── Monitor existing positions ───────────────────────────
                prev_tickets = self.pos_monitor.get_open_tickets()
                self.pos_monitor.check_all_positions()
                self.pos_monitor.handle_closed_positions(prev_tickets)

                # ── Weekend gap protection ────────────────────────────────
                self.pos_monitor.check_weekend_protection()

                # ── Periodic risk check (drawdown, peak balance) ─────────
                self.risk_mgr.periodic_risk_check()

                # ── Check risk status ────────────────────────────────────
                if self.risk_mgr.is_halted:
                    log.info(
                        f"Trading halted: {self.risk_mgr.halt_reason} — "
                        "monitoring positions only"
                    )
                    self._inter_cycle_surveillance(cfg.POSITION_CHECK_SECONDS)
                    continue

                # ── Market open check ────────────────────────────────────
                # Forex is closed on weekends, but crypto trades 24/7.
                # If forex is closed, we still run the scan (crypto symbols
                # pass session filtering) and the surveillance loop.
                forex_open = market_hours.is_market_open()
                forex_new_allowed = market_hours.is_new_trade_allowed()

                if not forex_open:
                    if self._cycle_count % 60 == 1:
                        log.info(
                            "Forex market closed — scanning crypto only"
                        )
                elif not forex_new_allowed:
                    if self._cycle_count % 30 == 1:
                        log.info(
                            "Friday wind-down — forex: managing existing "
                            "positions only, crypto: still scanning for new trades"
                        )

                # ── Full scan → populates watchlist ──────────────────────
                # All qualifying setups (score ≥ WATCHLIST_SETUP_THRESHOLD)
                # go to the watchlist.  No signals are returned here.
                # Signals come from trigger detection in the surveillance loop.
                self.scanner.full_scan()

                # ── Scan-only: show watchlist status ──────────────────────
                if self.scan_only:
                    wl = self.scanner.watchlist
                    entries = wl.entries_sorted()
                    if entries:
                        log.info(
                            f"[SCAN ONLY] {len(entries)} symbols on watchlist "
                            "(not executing, waiting for triggers)"
                        )
                        for e in entries[:5]:
                            log.info(
                                f"  → {e.direction:4s} {e.symbol:12s} "
                                f"conf={e.confluence_score:5.1f}  "
                                f"setup={e.setup_score:5.1f}  "
                                f"checks={e.checks}"
                            )

                # ── Daily summary ────────────────────────────────────────
                self._check_daily_summary()

                # ── Day/week boundary resets ──────────────────────────────
                self._check_period_resets()

                # ── Inter-cycle: position mgmt + watchlist stalking ─────
                # Between full scans, two activities run concurrently:
                #   1. Fast tick surveillance on open positions (every 5s)
                #   2. Watchlist trigger detection on M15 bars (every 15s)
                # When a trigger fires → chart analysis → execution.
                elapsed = time.time() - cycle_start
                remaining = max(1, cfg.SCAN_INTERVAL_SECONDS - elapsed)
                log.debug(
                    f"Cycle #{self._cycle_count} done in {elapsed:.1f}s — "
                    f"tick surveillance for {remaining:.0f}s"
                )
                self._inter_cycle_surveillance(remaining)

            except Exception as e:
                log.error(f"Error in main loop cycle: {e}", exc_info=True)
                time.sleep(10)

    def _main_loop_sniper(self):
        """
        Event-driven M15 sniper loop.
        Triggers on new M15 bars and monitors intrabar for top candidates.
        """
        global _shutdown_requested

        last_forming_time = 0
        last_universe_refresh = 0
        last_position_check = 0

        while not _shutdown_requested:
            try:
                self.mt5.ensure_connected()

                if time.time() - last_universe_refresh > 1800 and self.sniper:
                    self.sniper.refresh_universe()
                    last_universe_refresh = time.time()

                # Position management (throttled)
                if time.time() - last_position_check >= cfg.POSITION_CHECK_SECONDS:
                    last_position_check = time.time()
                    prev_tickets = self.pos_monitor.get_open_tickets()
                    self.pos_monitor.check_all_positions()
                    self.pos_monitor.handle_closed_positions(prev_tickets)
                    self.pos_monitor.check_weekend_protection()
                    self.risk_mgr.periodic_risk_check()

                if self.risk_mgr.is_halted:
                    time.sleep(cfg.POSITION_CHECK_SECONDS)
                    continue

                # Bar-close detection using a reference symbol
                ref_symbol = self.sniper.universe[0] if self.sniper and self.sniper.universe else ""
                if ref_symbol:
                    forming_time = self.sniper.get_forming_bar_time(ref_symbol)
                    if forming_time and forming_time != last_forming_time:
                        last_forming_time = forming_time
                        intents = self.sniper.on_bar_close()
                        self._process_intents(intents)

                # Intrabar monitoring
                if self.sniper:
                    intents = self.sniper.intrabar_check()
                    self._process_intents(intents)

                # Fast tick surveillance for open positions
                self.pos_monitor.fast_check_all_positions()

                # Daily summary and resets
                self._check_daily_summary()
                self._check_period_resets()

                time.sleep(2)

            except Exception as e:
                log.error(f"Error in sniper loop: {e}", exc_info=True)
                time.sleep(5)

    def _process_intents(self, intents: list):
        """Process and execute sniper execution intents."""
        if not intents:
            return
        for intent in intents:
            if _shutdown_requested:
                break
            if self.scan_only:
                log.info(
                    f"[SCAN ONLY] {intent.symbol} {intent.direction} "
                    f"type={intent.entry_type} setup={intent.setup_type}"
                )
                continue
            # Max concurrent positions guard
            if len(self.mt5.our_positions()) >= cfg.MAX_CONCURRENT_POSITIONS:
                log.info("Max concurrent positions reached — skipping intents")
                break

            if not market_hours.is_new_trade_allowed(symbol=intent.symbol):
                log.info(
                    f"{intent.symbol}: skipped — new trades not allowed "
                    f"(Friday wind-down / market closed)"
                )
                continue

            allowed, reason = self.risk_mgr.can_open_trade(
                intent.symbol,
                direction=intent.direction,
                current_price=intent.entry_price,
                atr=intent.atr,
            )
            if not allowed:
                log.info(f"{intent.symbol}: blocked — {reason}")
                continue

            result = self.executor.execute_intent(intent)
            if result:
                log.info(
                    f"Sniper intent executed: {intent.direction} {intent.symbol} "
                    f"type={intent.entry_type}"
                )

    def _inter_cycle_surveillance(self, duration_seconds: float):
        """
        Run fast tick checks AND watchlist trigger detection for the
        specified duration, then return.

        Two concurrent activities between full scan cycles:

        1. Position management (every TICK_CHECK_SECONDS = 5s):
           Fast tick surveillance on open positions using symbol_info_tick
           (local memory — near-zero latency).

        2. Watchlist stalking (every WATCHLIST_CHECK_SECONDS = 15s):
           Check watchlisted symbols for M15 trigger patterns.
           When a trigger fires → chart analysis (~45-50s) → execute.
           During chart analysis, position management pauses.  Positions
           are protected by broker-side SL/TP during this window.

        This is the "stalking screen" — the pro-trader Phase 2/3 behaviour
        that bridges the gap between setup identification and entry trigger.
        """
        global _shutdown_requested
        end_time = time.time() + duration_seconds
        tick_interval = cfg.TICK_CHECK_SECONDS
        last_watchlist_check = 0.0

        while time.time() < end_time and not _shutdown_requested:
            # Sleep first, then check — gives the market time to move
            sleep_chunk = min(tick_interval, end_time - time.time())
            if sleep_chunk <= 0:
                break
            # Sleep in 1s increments for shutdown responsiveness
            for _ in range(max(1, int(sleep_chunk))):
                if _shutdown_requested or time.time() >= end_time:
                    break
                time.sleep(1)

            if _shutdown_requested:
                break

            # ── 1. Fast tick surveillance on open positions ───────────
            try:
                self.pos_monitor.fast_check_all_positions()
            except Exception as e:
                log.error(f"Fast tick surveillance error: {e}")

            # ── 2. Watchlist trigger detection (the stalking) ─────────
            # Only check every WATCHLIST_CHECK_SECONDS (default 15s) to
            # avoid excessive M15 bar fetches.
            now = time.time()
            if (now - last_watchlist_check >= cfg.WATCHLIST_CHECK_SECONDS
                    and not self.scan_only):
                last_watchlist_check = now
                try:
                    triggered = self.scanner.watchlist_check()
                    if triggered:
                        # Process triggered signals: risk check → chart
                        # analysis (~45-50s) → execution.
                        # Position management pauses during chart analysis.
                        # Positions are protected by broker-side SL/TP.
                        self._process_signals(triggered)
                except Exception as e:
                    log.error(f"Watchlist trigger check error: {e}")

    def _process_signals(self, signals: list[TradeSignal]):
        """Process and execute qualifying signals."""
        for signal in signals:
            if _shutdown_requested:
                break

            # Per-symbol market hours check — crypto is always allowed,
            # forex/CFDs are blocked during Friday wind-down & market close
            if not market_hours.is_new_trade_allowed(symbol=signal.symbol):
                log.info(
                    f"{signal.symbol}: skipped — new trades not allowed "
                    f"(Friday wind-down / market closed)"
                )
                continue

            # Check risk one more time (with fresh setup parameters)
            allowed, reason = self.risk_mgr.can_open_trade(
                signal.symbol,
                direction=signal.direction,
                current_price=signal.entry_price,
                atr=signal.atr,
            )
            if not allowed:
                log.info(f"{signal.symbol}: blocked — {reason}")
                continue

            # ── GPT-5.2 Visual Chart Analysis (two-tier) ─────────────────
            # Tier 1: Render CLEAN M15/H1/H4/D1 charts, send to GPT for
            #         unbiased visual analysis (no direction hint, no levels).
            # Tier 2: Render ANNOTATED charts (Entry/SL/TP drawn), send to
            #         GPT with the Tier 1 report.  GPT can now see our exact
            #         levels on the chart and assess geometric validity.
            #
            # The risk_factor (0.5–1.0) scales position size.
            # VETO RULE: For marginal setups (score < 65), if chart analysis
            #   says "contradictory" with risk_factor < 0.6, we SKIP the trade.
            #   This is NOT AI vetoing — it's two independent sources (low
            #   algorithmic score + bad chart geometry) both saying "weak setup."
            score_breakdown = {}
            sa = self.scanner.get_analysis(signal.symbol)
            if sa:
                score_breakdown = getattr(sa, "score_breakdown", {})

            chart_vetoed = False
            chart_result = None  # initialized before try for journal access
            try:
                chart_result = chart_analyst.analyze_signal_charts(
                    self.mt5, signal.to_dict(), score_breakdown,
                )

                chart_rf = chart_result.get("risk_factor", 1.0)
                alignment = chart_result.get("alignment", "unavailable")
                red_flags = chart_result.get("red_flags", [])
                supports = chart_result.get("supports", [])
                elapsed = chart_result.get("elapsed_seconds", 0)
                sl_assessment = chart_result.get("sl_assessment", "")
                tp_assessment = chart_result.get("tp_assessment", "")

                # Apply chart risk factor to signal's tier-based factor
                pre_chart_rf = signal.risk_factor
                signal.risk_factor *= chart_rf

                log.info(
                    f"{signal.symbol}: chart analysis → "
                    f"alignment={alignment} chart_rf={chart_rf:.2f} "
                    f"(tier={pre_chart_rf:.2f} × chart={chart_rf:.2f} "
                    f"= effective={signal.risk_factor:.2f}) "
                    f"in {elapsed:.1f}s"
                )
                if sl_assessment:
                    log.info(f"{signal.symbol}: SL → {sl_assessment}")
                if tp_assessment:
                    log.info(f"{signal.symbol}: TP → {tp_assessment}")

                # ── CHART VETO for marginal setups ───────────────────────
                # If the algorithmic score is already marginal (< 65) AND
                # chart analysis independently says "contradictory" with a
                # low risk factor (< 0.6), the trade thesis is weak from
                # BOTH sources.  A pro trader would walk away.
                if (signal.confidence < 65
                        and alignment == "contradictory"
                        and chart_rf < 0.6):
                    chart_vetoed = True
                    log.warning(
                        f"{signal.symbol}: CHART VETO — marginal setup "
                        f"(conf={signal.confidence:.0f} < 65) + contradictory "
                        f"chart analysis (rf={chart_rf:.2f} < 0.6).  "
                        f"Skipping trade."
                    )
                    self.alerter.custom(
                        f"<b>🚫 CHART VETO</b>\n"
                        f"{signal.direction} {signal.symbol} "
                        f"conf={signal.confidence:.0f}\n"
                        f"Chart: <b>contradictory</b> (rf={chart_rf:.2f})\n"
                        f"Reason: low-confidence setup ({signal.confidence:.0f}) "
                        f"+ charts don't support it\n"
                        f"SL: {sl_assessment}\n"
                        f"TP: {tp_assessment}"
                    )

                # Add to rationale for trade journal
                signal.rationale.append(
                    f"Chart analysis: {alignment} "
                    f"(risk_factor={signal.risk_factor:.2f})"
                )
                if sl_assessment:
                    signal.rationale.append(f"  SL: {sl_assessment}")
                if tp_assessment:
                    signal.rationale.append(f"  TP: {tp_assessment}")
                if red_flags:
                    for flag in red_flags[:3]:
                        signal.rationale.append(f"  ⚠ {flag}")
                if supports:
                    for sup in supports[:3]:
                        signal.rationale.append(f"  ✓ {sup}")

                # Send detailed Telegram alert with chart analysis
                if not chart_vetoed:
                    rf_emoji = "✓" if chart_rf >= 0.85 else "⚠" if chart_rf >= 0.65 else "🚨"
                    flags_text = "\n".join(f"⚠ {f}" for f in red_flags[:3]) if red_flags else "None"
                    sups_text = "\n".join(f"✓ {s}" for s in supports[:3]) if supports else "None"
                    sl_text = f"\n<b>SL:</b> {sl_assessment}" if sl_assessment else ""
                    tp_text = f"\n<b>TP:</b> {tp_assessment}" if tp_assessment else ""

                    self.alerter.custom(
                        f"<b>{rf_emoji} CHART ANALYSIS</b>\n"
                        f"{signal.direction} {signal.symbol} "
                        f"conf={signal.confidence:.0f}\n"
                        f"Alignment: <b>{alignment}</b>\n"
                        f"Risk factor: {signal.risk_factor:.2f} "
                        f"(tier={pre_chart_rf:.2f} × chart={chart_rf:.2f})"
                        f"{sl_text}{tp_text}\n\n"
                        f"<b>Red flags:</b>\n{flags_text}\n\n"
                        f"<b>Supports:</b>\n{sups_text}"
                    )

            except Exception as e:
                log.warning(f"{signal.symbol}: chart analysis error (non-blocking): {e}")

            # ── Skip execution if chart vetoed ────────────────────────────
            if chart_vetoed:
                continue

            # Execute
            result = self.executor.execute_signal(signal)
            if result:
                log.info(
                    f"Trade executed: {signal.direction} {signal.symbol} "
                    f"risk_factor={signal.risk_factor:.2f}"
                )

                # ── Log chart analysis to trade journal (supplementary record)
                # This creates a paired CHART_ANALYSIS journal entry so the
                # full analysis is preserved alongside the OPEN record.
                try:
                    chart_journal = {
                        "action": "CHART_ANALYSIS",
                        "symbol": signal.symbol,
                        "direction": signal.direction,
                        "order_ticket": result.get("order_ticket", 0),
                        "alignment": chart_result.get("alignment", "unavailable") if chart_result else "unavailable",
                        "chart_risk_factor": chart_result.get("risk_factor", 1.0) if chart_result else 1.0,
                        "effective_risk_factor": signal.risk_factor,
                        "sl_assessment": chart_result.get("sl_assessment", "") if chart_result else "",
                        "tp_assessment": chart_result.get("tp_assessment", "") if chart_result else "",
                        "red_flags": chart_result.get("red_flags", []) if chart_result else [],
                        "supports": chart_result.get("supports", []) if chart_result else [],
                        "analysis_dir": chart_result.get("analysis_dir", "") if chart_result else "",
                        "elapsed_seconds": chart_result.get("elapsed_seconds", 0) if chart_result else 0,
                    }
                    self.risk_mgr.log_trade(chart_journal)
                except Exception as e:
                    log.warning(f"Failed to log chart analysis to journal: {e}")

                # Don't open more trades than allowed
                our_pos = self.mt5.our_positions()
                if len(our_pos) >= cfg.MAX_CONCURRENT_POSITIONS:
                    log.info("Max concurrent positions reached — stopping signal processing")
                    break

    def _check_daily_summary(self):
        """Send daily summary at configured hour."""
        now = market_hours.utcnow()
        if now.hour == cfg.DAILY_SUMMARY_HOUR_UTC and self._last_daily_summary != now.day:
            self._last_daily_summary = now.day

            stats = self.risk_mgr.daily_stats
            balance = self.mt5.account_balance()
            positions = self.mt5.our_positions()

            self.alerter.daily_summary(
                balance=balance,
                starting_balance=cfg.TRADING_CAPITAL,
                trades_today=stats["trades_today"],
                wins_today=stats["wins_today"],
                pnl_today=stats["daily_pnl"],
                open_trades=len(positions),
            )

            # Generate AI briefing
            top_opps = self.scanner.top_opportunities(10)
            if top_opps:
                briefing = ai_analyst.generate_market_briefing(top_opps)
                if briefing:
                    self.alerter.custom(f"<b>DAILY BRIEFING</b>\n\n{briefing}")

            log.info(
                f"Daily summary: PnL=${stats['daily_pnl']:+.2f} "
                f"trades={stats['trades_today']} wins={stats['wins_today']} "
                f"balance=${balance:,.2f}"
            )

    def _check_period_resets(self):
        """Reset daily/weekly counters at period boundaries (once per period)."""
        now = market_hours.utcnow()
        # New day reset — only fire once per calendar day
        if now.hour == 0 and now.minute < 2 and self._last_daily_reset_day != now.day:
            self._last_daily_reset_day = now.day
            self.risk_mgr.reset_daily()
            log.info("Daily risk counters reset")
        # New week reset on Monday — only fire once per calendar week
        if now.weekday() == 0 and now.hour == 0 and now.minute < 2 and self._last_weekly_reset_week != now.isocalendar()[1]:
            self._last_weekly_reset_week = now.isocalendar()[1]
            self.risk_mgr.reset_weekly()
            log.info("Weekly risk counters reset")

    def _shutdown(self):
        """Clean shutdown — exception-safe so every step runs."""
        log.info("Shutting down Wolf Trading System …")

        balance = 0.0
        positions = []
        try:
            balance = self.mt5.account_balance()
            positions = self.mt5.our_positions()
        except Exception as e:
            log.warning(f"Could not fetch account data during shutdown: {e}")

        try:
            self.alerter.bot_status(
                "STOPPED",
                f"Balance: ${balance:,.2f}\n"
                f"Open positions: {len(positions)}\n"
                f"Total cycles: {self._cycle_count}",
            )
        except Exception as e:
            log.warning(f"Could not send shutdown alert: {e}")

        try:
            self.mt5.disconnect()
        except Exception as e:
            log.warning(f"Error during MT5 disconnect: {e}")

        log.info("Wolf Trading System stopped.")


# ═════════════════════════════════════════════════════════════════════════════
#  STATUS COMMAND
# ═════════════════════════════════════════════════════════════════════════════

def show_status():
    """Quick status check — connect, print info, disconnect."""
    mt5_conn = MT5Connector()
    if not mt5_conn.connect():
        print("ERROR: Cannot connect to MT5")
        return

    acc = mt5_conn.account_info()
    print("\n" + "=" * 60)
    print("  WOLF TRADING SYSTEM — Account Status")
    print("=" * 60)
    print(f"  Account:    {acc.get('login')}")
    print(f"  Server:     {acc.get('server')}")
    print(f"  Balance:    ${acc.get('balance', 0):,.2f}")
    print(f"  Equity:     ${acc.get('equity', 0):,.2f}")
    print(f"  Margin:     ${acc.get('margin', 0):,.2f}")
    print(f"  Free Margin:${acc.get('margin_free', 0):,.2f}")
    print(f"  Leverage:   1:{acc.get('leverage', 0)}")
    print(f"  Profit:     ${acc.get('profit', 0):,.2f}")

    positions = mt5_conn.our_positions()
    print(f"\n  Open positions (WOLF): {len(positions)}")
    for pos in positions:
        sym = pos.get("symbol", "?")
        direction = "BUY" if pos.get("type", 0) == 0 else "SELL"
        pnl = pos.get("profit", 0)
        vol = pos.get("volume", 0)
        print(f"    {sym:12s} {direction:4s} {vol:.2f} lots  PnL=${pnl:+.2f}")

    symbols = mt5_conn.get_symbols_by_groups()
    print(f"\n  Tradeable symbols: {len(symbols)}")

    sessions = market_hours.active_sessions()
    print(f"  Active sessions:  {', '.join(sessions) or 'None'}")
    print(f"  Market open:      {market_hours.is_market_open()}")
    print("=" * 60 + "\n")

    mt5_conn.disconnect()


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Wolf Trading System")
    parser.add_argument(
        "--scan-only", action="store_true",
        help="Scan and report opportunities without executing trades",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show account status and exit",
    )
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    engine = WolfEngine(scan_only=args.scan_only)
    engine.start()


if __name__ == "__main__":
    main()
