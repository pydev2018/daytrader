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
from core.signals import TradeSignal, confidence_to_win_probability
from core import ai_analyst
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
                if not market_hours.is_market_open():
                    if self._cycle_count % 60 == 1:
                        log.info("Market closed — waiting …")
                    time.sleep(cfg.SCAN_INTERVAL_SECONDS)
                    continue

                # ── Friday wind-down check ─────────────────────────────
                # After Friday 12:00 ET: no new trades.  Existing positions
                # are still managed by check_all_positions() above.
                # The weekend emergency close at 16:30 ET is the backstop.
                if not market_hours.is_new_trade_allowed():
                    if self._cycle_count % 30 == 1:
                        log.info(
                            "Friday wind-down — no new trades, "
                            "managing existing positions only"
                        )
                    elapsed = time.time() - cycle_start
                    remaining = max(1, cfg.SCAN_INTERVAL_SECONDS - elapsed)
                    self._inter_cycle_surveillance(remaining)
                    continue

                # ── Full scan ────────────────────────────────────────────
                signals = self.scanner.full_scan()

                # ── Process signals ──────────────────────────────────────
                if signals and not self.scan_only:
                    self._process_signals(signals)
                elif signals and self.scan_only:
                    log.info(f"[SCAN ONLY] {len(signals)} signals found (not executing)")
                    for sig in signals[:3]:
                        log.info(
                            f"  → {sig.direction} {sig.symbol} "
                            f"conf={sig.confidence:.1f} R:R=1:{sig.risk_reward_ratio}"
                        )

                # ── Daily summary ────────────────────────────────────────
                self._check_daily_summary()

                # ── Day/week boundary resets ──────────────────────────────
                self._check_period_resets()

                # ── Inter-cycle tick surveillance ───────────────────────
                # Instead of sleeping idle, run fast tick checks every
                # TICK_CHECK_SECONDS until the next full analysis cycle.
                # Fast checks use only symbol_info_tick (free, local memory)
                # and cached thresholds — no bar fetches, no CPU-heavy work.
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

    def _inter_cycle_surveillance(self, duration_seconds: float):
        """
        Run fast tick checks for the specified duration, then return.

        This fills the gap between full 60s analysis cycles with lightweight
        tick-level surveillance.  Each check costs only 1 × symbol_info_tick
        per open position (local memory read — near-zero latency).

        Catches rapid adverse moves and macro S/R zone entries within
        TICK_CHECK_SECONDS instead of waiting up to SCAN_INTERVAL_SECONDS.
        """
        global _shutdown_requested
        end_time = time.time() + duration_seconds
        tick_interval = cfg.TICK_CHECK_SECONDS

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

            # Fast tick surveillance on all open positions
            try:
                self.pos_monitor.fast_check_all_positions()
            except Exception as e:
                log.error(f"Fast tick surveillance error: {e}")

    def _process_signals(self, signals: list[TradeSignal]):
        """Process and execute qualifying signals."""
        for signal in signals:
            if _shutdown_requested:
                break

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

            # Informational AI review — logged and alerted, NEVER blocks
            # The Pristine method is the sole decision-maker.
            # AI adds a second opinion for the trade journal / Telegram,
            # but it cannot veto, reject, or weaken a signal.
            if cfg.OPENAI_API_KEY and signal.confidence >= 85:
                try:
                    review = ai_analyst.review_trade(signal.to_dict())
                    if review:
                        reasoning = review.get("reasoning", "")
                        risk_notes = review.get("risk_notes", "")
                        approved = review.get("approval", True)
                        adj = review.get("confidence_adjustment", 0)

                        log.info(
                            f"AI review for {signal.symbol} (INFO ONLY): "
                            f"opinion={approved} adj={adj:+d} — {reasoning}"
                        )
                        signal.rationale.append(
                            f"AI opinion: {'favourable' if approved else 'cautious'} "
                            f"({reasoning})"
                        )
                        if risk_notes:
                            signal.rationale.append(f"AI risk note: {risk_notes}")

                        # Send to Telegram so you see the AI's opinion
                        self.alerter.custom(
                            f"<b>AI REVIEW (info only)</b>\n"
                            f"{signal.direction} {signal.symbol} "
                            f"conf={signal.confidence:.0f}\n"
                            f"Opinion: {'✓ favourable' if approved else '⚠ cautious'}\n"
                            f"{reasoning}"
                        )
                except Exception as e:
                    log.warning(f"AI review failed (non-blocking): {e}")

            # Execute
            result = self.executor.execute_signal(signal)
            if result:
                log.info(f"Trade executed: {signal.direction} {signal.symbol}")

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
