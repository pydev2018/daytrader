"""
===============================================================================
  Position Monitor — manages open positions in real-time
===============================================================================
  Handles:
  • Trailing stop adjustments
  • Breakeven moves
  • Partial take-profit
  • Emergency exits
  • PnL tracking
===============================================================================
"""

from __future__ import annotations

from typing import Optional

import numpy as np

import MetaTrader5 as mt5
import config as cfg
from core.mt5_connector import MT5Connector
from core.indicators import add_atr
from execution.trade_executor import TradeExecutor
from risk.risk_manager import RiskManager
from alerts.telegram import TelegramAlerter
from utils.logger import get_logger

log = get_logger("pos_monitor")


class PositionMonitor:
    """Monitors and manages all open positions."""

    def __init__(
        self,
        mt5_conn: MT5Connector,
        executor: TradeExecutor,
        risk_mgr: RiskManager,
        alerter: TelegramAlerter,
    ):
        self.mt5 = mt5_conn
        self.executor = executor
        self.risk = risk_mgr
        self.alerter = alerter
        # Track which positions have been partially closed
        self._partial_closed: set[int] = set()
        # Track which positions have been moved to breakeven
        self._breakeven_set: set[int] = set()

    def check_all_positions(self):
        """
        Main monitoring loop — called every POSITION_CHECK_SECONDS.
        """
        positions = self.mt5.our_positions()
        if not positions:
            return

        for pos in positions:
            try:
                self._manage_position(pos)
            except Exception as e:
                log.error(f"Error managing position {pos.get('ticket', '?')}: {e}")

    def _manage_position(self, pos: dict):
        """Apply management rules to a single position."""
        ticket = pos.get("ticket", 0)
        symbol = pos.get("symbol", "")
        pos_type = pos.get("type", 0)
        open_price = pos.get("price_open", 0)
        current_profit = pos.get("profit", 0)
        volume = pos.get("volume", 0)
        sl = pos.get("sl", 0)
        tp = pos.get("tp", 0)

        # Get current price
        tick = self.mt5.symbol_tick(symbol)
        if tick is None:
            return

        current_price = tick["bid"] if pos_type == mt5.ORDER_TYPE_BUY else tick["ask"]

        # Get ATR for dynamic calculations — use H1 for more breathing room
        # (trades are based on H1+ analysis; M15 ATR is too tight)
        df = self.mt5.get_rates(symbol, "H1", count=50)
        if df is None or len(df) < 20:
            # Fallback to M15 if H1 not available
            df = self.mt5.get_rates(symbol, "M15", count=50)
            if df is None or len(df) < 20:
                return
        df = add_atr(df)
        current_atr = df["atr"].iloc[-1]
        if np.isnan(current_atr) or current_atr == 0:
            return

        # Calculate R-multiple (how many R's of profit)
        risk_distance = abs(open_price - sl) if sl != 0 else current_atr * cfg.ATR_SL_MULTIPLIER
        if risk_distance == 0:
            return

        if pos_type == mt5.ORDER_TYPE_BUY:
            current_r = (current_price - open_price) / risk_distance
        else:
            current_r = (open_price - current_price) / risk_distance

        # Get spread buffer for this symbol
        sym_info = self.mt5.symbol_info(symbol)
        spread_buffer = 0
        if sym_info:
            spread_buffer = sym_info.get("spread", 0) * sym_info.get("point", 0.00001) * 2

        # ── 1. Breakeven move ────────────────────────────────────────────
        if (
            ticket not in self._breakeven_set
            and current_r >= cfg.TRAILING_STOP_ACTIVATE_R
        ):
            if pos_type == mt5.ORDER_TYPE_BUY:
                new_sl = open_price + spread_buffer
                if new_sl > sl:
                    if self.executor.modify_sl_tp(pos, new_sl=new_sl):
                        self._breakeven_set.add(ticket)
                        sl = new_sl  # update local SL to prevent stale-data issues
                        log.info(f"{symbol} #{ticket}: moved to breakeven @ {new_sl:.5f}")
            else:
                new_sl = open_price - spread_buffer
                if sl == 0 or new_sl < sl:
                    if self.executor.modify_sl_tp(pos, new_sl=new_sl):
                        self._breakeven_set.add(ticket)
                        sl = new_sl
                        log.info(f"{symbol} #{ticket}: moved to breakeven @ {new_sl:.5f}")

        # ── 2. Trailing stop (only if breakeven already done) ─────────────
        elif ticket in self._breakeven_set and current_r >= cfg.TRAILING_STOP_ACTIVATE_R + 0.5:
            trail_distance = current_atr * cfg.TRAILING_STOP_DISTANCE_ATR + spread_buffer

            if pos_type == mt5.ORDER_TYPE_BUY:
                new_trail_sl = current_price - trail_distance
                if new_trail_sl > sl and new_trail_sl > open_price:
                    self.executor.modify_sl_tp(pos, new_sl=new_trail_sl)
                    log.debug(f"{symbol} #{ticket}: trail SL → {new_trail_sl:.5f}")
            else:
                new_trail_sl = current_price + trail_distance
                if (sl == 0 or new_trail_sl < sl) and new_trail_sl < open_price:
                    self.executor.modify_sl_tp(pos, new_sl=new_trail_sl)
                    log.debug(f"{symbol} #{ticket}: trail SL → {new_trail_sl:.5f}")

        # ── 3. Partial take-profit ───────────────────────────────────────
        vol_min = sym_info.get("volume_min", 0.01) if sym_info else 0.01
        can_split = volume * cfg.PARTIAL_TP_RATIO >= vol_min and volume * (1 - cfg.PARTIAL_TP_RATIO) >= vol_min
        if (
            ticket not in self._partial_closed
            and current_r >= cfg.PARTIAL_TP_RR
            and can_split
        ):
            close_result = self.executor.close_position(
                pos, reason="partial_tp", partial=cfg.PARTIAL_TP_RATIO
            )
            if close_result:
                self._partial_closed.add(ticket)
                pnl = close_result.get("pnl", 0)
                self.alerter.custom(
                    f"<b>PARTIAL TP</b>\n"
                    f"{symbol} — closed {cfg.PARTIAL_TP_RATIO:.0%} at {current_r:.1f}R\n"
                    f"P/L: ${pnl:,.2f}"
                )

    def handle_closed_positions(self, previously_open: set[int]):
        """
        Detect positions that have been closed (by SL/TP) since last check.
        Record the result in the risk manager and send alerts.
        """
        current_tickets = {
            pos.get("ticket", 0) for pos in self.mt5.our_positions()
        }
        closed_tickets = previously_open - current_tickets

        for ticket in closed_tickets:
            # Clean up tracking sets
            self._partial_closed.discard(ticket)
            self._breakeven_set.discard(ticket)

            # Query ALL deals for this position to compute true net PnL
            # (includes entry commission, partial close P/L, final close P/L, swaps)
            pnl = 0.0
            symbol = "?"
            try:
                deals = mt5.history_deals_get(position=ticket)
                if deals:
                    for deal in deals:
                        d = deal._asdict()
                        pnl += d.get("profit", 0) + d.get("commission", 0) + d.get("swap", 0)
                        symbol = d.get("symbol", symbol)
            except Exception as e:
                log.warning(f"Could not get deal history for #{ticket}: {e}")

            # Record in risk manager so daily/weekly limits work
            self.risk.record_trade_result(pnl, pnl > 0)

            log.info(f"Position #{ticket} ({symbol}) closed — PnL=${pnl:.2f}")

            # Send alert
            try:
                balance = self.mt5.account_balance()
                self.alerter.custom(
                    f"<b>POSITION CLOSED</b>\n"
                    f"{symbol} #{ticket}\n"
                    f"P/L: ${pnl:+,.2f}\n"
                    f"Balance: ${balance:,.2f}"
                )
            except Exception:
                pass

    def get_open_tickets(self) -> set[int]:
        """Return set of our open position tickets."""
        return {pos.get("ticket", 0) for pos in self.mt5.our_positions()}

    def emergency_close_all(self, reason: str = "emergency"):
        """Close ALL our open positions immediately."""
        positions = self.mt5.our_positions()
        closed = 0
        failed = 0
        for pos in positions:
            result = self.executor.close_position(pos, reason=reason)
            if result:
                closed += 1
            else:
                failed += 1
                # Retry once
                result = self.executor.close_position(pos, reason=reason)
                if result:
                    closed += 1
                    failed -= 1

        if positions:
            status = f"Closed {closed}/{len(positions)} positions."
            if failed:
                status += f" FAILED to close {failed}!"
            self.alerter.safety_event(
                "EMERGENCY CLOSE ALL",
                f"{status} Reason: {reason}",
                self.mt5.account_balance(),
            )
            log.warning(f"EMERGENCY: {status} — {reason}")
