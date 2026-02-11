"""
===============================================================================
  Trade Executor — sends orders to MT5 with full validation
===============================================================================
"""

from __future__ import annotations

from typing import Optional

import MetaTrader5 as mt5

import config as cfg

# Bitmask flags for symbol filling_mode (NOT the same as ORDER_FILLING_* enums)
_SYMBOL_FILL_FOK = 1   # bit 0 — Fill or Kill supported
_SYMBOL_FILL_IOC = 2   # bit 1 — Immediate or Cancel supported
from core.mt5_connector import MT5Connector
from core.signals import TradeSignal
from risk.position_sizer import compute_position_size
from risk.risk_manager import RiskManager
from alerts.telegram import TelegramAlerter
from utils.logger import get_logger

log = get_logger("executor")


class TradeExecutor:
    """Validates and executes trade signals via MT5."""

    def __init__(
        self,
        mt5_conn: MT5Connector,
        risk_mgr: RiskManager,
        alerter: TelegramAlerter,
    ):
        self.mt5 = mt5_conn
        self.risk = risk_mgr
        self.alerter = alerter

    def execute_signal(self, signal: TradeSignal) -> Optional[dict]:
        """
        Full execution pipeline:
        1. Risk check
        2. Position sizing
        3. Order validation
        4. Order submission
        5. Logging & alerts
        """
        symbol = signal.symbol

        # ── Step 1: Risk gate ────────────────────────────────────────────
        allowed, reason = self.risk.can_open_trade(symbol)
        if not allowed:
            log.info(f"{symbol}: blocked by risk manager — {reason}")
            return None

        # ── Step 2: Position sizing ──────────────────────────────────────
        lots = compute_position_size(
            self.mt5,
            symbol,
            signal.direction,
            signal.entry_price,
            signal.stop_loss,
            signal.confidence,
            signal.risk_reward_ratio,
            adjusted_risk_pct=self.risk.adjusted_risk_pct(),
        )
        if lots <= 0:
            log.info(f"{symbol}: position size = 0 — skip")
            return None

        # ── Step 3: Build order request ──────────────────────────────────
        if not self.mt5.select_symbol(symbol):
            log.error(f"Cannot select {symbol}")
            return None

        tick = self.mt5.symbol_tick(symbol)
        if tick is None:
            log.error(f"Cannot get tick for {symbol}")
            return None

        sym_info = self.mt5.symbol_info(symbol)
        if sym_info is None:
            return None

        digits = sym_info.get("digits", 5)

        if signal.direction == "BUY":
            order_type = mt5.ORDER_TYPE_BUY
            price = tick["ask"]
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick["bid"]

        sl = round(signal.stop_loss, digits)
        tp = round(signal.take_profit, digits)

        # Determine filling mode using BITMASK flags (not ORDER_FILLING enums)
        filling_modes = sym_info.get("filling_mode", 0)
        if filling_modes & _SYMBOL_FILL_FOK:
            filling = mt5.ORDER_FILLING_FOK
        elif filling_modes & _SYMBOL_FILL_IOC:
            filling = mt5.ORDER_FILLING_IOC
        else:
            filling = mt5.ORDER_FILLING_RETURN

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lots,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": cfg.MAGIC_NUMBER,
            "comment": f"wolf_{signal.confidence:.0f}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        # ── Step 4: Validate ─────────────────────────────────────────────
        check = self.mt5.check_order(request)
        if check is None:
            log.error(f"{symbol}: order_check returned None")
            return None

        retcode = check.get("retcode", 0)
        if retcode != 0:
            comment = check.get("comment", "unknown")
            log.warning(f"{symbol}: order_check failed — retcode={retcode} comment={comment}")
            # Try with RETURN filling if FOK fails
            if "filling" in comment.lower() or retcode == 10030:
                request["type_filling"] = mt5.ORDER_FILLING_RETURN
                check = self.mt5.check_order(request)
                if check is None or check.get("retcode", 0) != 0:
                    log.error(f"{symbol}: order_check failed even with RETURN filling")
                    return None
            else:
                return None

        # ── Step 5: Execute ──────────────────────────────────────────────
        result = self.mt5.send_order(request)
        if result is None:
            log.error(f"{symbol}: order_send returned None")
            return None

        retcode = result.get("retcode", 0)
        if retcode != mt5.TRADE_RETCODE_DONE:
            comment = result.get("comment", "unknown")
            log.error(
                f"{symbol}: order REJECTED — retcode={retcode} comment={comment}"
            )
            return None

        # ── Step 6: Log & alert ──────────────────────────────────────────
        order_ticket = result.get("order", 0)
        deal_ticket = result.get("deal", 0)
        filled_price = result.get("price", price)
        filled_volume = result.get("volume", lots)

        trade_record = {
            "action": "OPEN",
            "symbol": symbol,
            "direction": signal.direction,
            "volume": filled_volume,
            "entry_price": filled_price,
            "stop_loss": sl,
            "take_profit": tp,
            "confidence": signal.confidence,
            "win_probability": signal.win_probability,
            "risk_reward": signal.risk_reward_ratio,
            "order_ticket": order_ticket,
            "deal_ticket": deal_ticket,
            "rationale": signal.rationale,
        }

        self.risk.log_trade(trade_record)

        log.info(
            f"ORDER FILLED: {signal.direction} {symbol} "
            f"{filled_volume} lots @ {filled_price}  "
            f"SL={sl}  TP={tp}  ticket={order_ticket}"
        )

        # Telegram alert
        balance = self.mt5.account_balance()
        self.alerter.trade_opened(
            symbol=symbol,
            direction=signal.direction,
            volume=filled_volume,
            entry_price=filled_price,
            tp_price=tp,
            sl_price=sl,
            cycle=0,
            step=0,
            balance=balance,
        )
        # Also send detailed rationale
        rationale_text = "\n".join(f"  • {r}" for r in signal.rationale[:6])
        self.alerter.custom(
            f"<b>TRADE RATIONALE</b>\n"
            f"<b>{symbol}</b> {signal.direction}\n"
            f"Confidence: {signal.confidence:.1f}/100\n"
            f"Win Prob: {signal.win_probability:.0%}\n"
            f"R:R = 1:{signal.risk_reward_ratio:.1f}\n"
            f"\n{rationale_text}"
        )

        return trade_record

    def close_position(
        self,
        position: dict,
        reason: str = "manual",
        partial: float = 1.0,
    ) -> Optional[dict]:
        """
        Close an open position (fully or partially).

        Parameters
        ----------
        position : dict
            Position dict from MT5 (must have ticket, symbol, type, volume).
        reason : str
            Why we're closing (for logging).
        partial : float
            Fraction to close (1.0 = full, 0.5 = half).
        """
        symbol = position.get("symbol", "")
        ticket = position.get("ticket", 0)
        pos_type = position.get("type", 0)
        volume = position.get("volume", 0)

        if not self.mt5.select_symbol(symbol):
            return None

        sym_info = self.mt5.symbol_info(symbol)

        # Snap close_volume to symbol's volume constraints
        vol_min = sym_info.get("volume_min", 0.01) if sym_info else 0.01
        vol_step = sym_info.get("volume_step", 0.01) if sym_info else 0.01
        close_volume = volume * partial
        if vol_step > 0:
            close_volume = int(close_volume / vol_step) * vol_step
        close_volume = round(close_volume, 8)  # float precision cleanup

        if close_volume < vol_min:
            log.warning(f"Close volume {close_volume} < vol_min {vol_min} for {symbol} — skip")
            return None

        # Also ensure the REMAINING volume is valid (or close in full)
        remaining = round(volume - close_volume, 8)
        if 0 < remaining < vol_min:
            close_volume = volume  # close in full if remainder would be below min

        tick = self.mt5.symbol_tick(symbol)
        if tick is None:
            return None
        filling = mt5.ORDER_FILLING_RETURN  # safe default
        if sym_info:
            fm = sym_info.get("filling_mode", 0)
            if fm & _SYMBOL_FILL_FOK:
                filling = mt5.ORDER_FILLING_FOK
            elif fm & _SYMBOL_FILL_IOC:
                filling = mt5.ORDER_FILLING_IOC
            else:
                filling = mt5.ORDER_FILLING_RETURN

        # Opposite order to close
        if pos_type == mt5.ORDER_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price = tick["bid"]
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = tick["ask"]

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": close_volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": cfg.MAGIC_NUMBER,
            "comment": f"wolf_close_{reason}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = self.mt5.send_order(request)
        if result is None or result.get("retcode") != mt5.TRADE_RETCODE_DONE:
            # Try RETURN filling
            request["type_filling"] = mt5.ORDER_FILLING_RETURN
            result = self.mt5.send_order(request)
            if result is None or result.get("retcode") != mt5.TRADE_RETCODE_DONE:
                log.error(f"Failed to close {symbol} #{ticket}: {result}")
                return None

        pnl = position.get("profit", 0)

        log.info(
            f"POSITION CLOSED: {symbol} #{ticket}  "
            f"volume={close_volume}  reason={reason}  PnL={pnl:.2f}"
        )

        # NOTE: PnL is NOT recorded to risk manager here to avoid double-counting.
        # The PositionMonitor.handle_closed_positions() records PnL when the
        # position fully closes (summing all deals for exact net P/L).

        # Journal
        trade_record = {
            "action": "CLOSE",
            "symbol": symbol,
            "ticket": ticket,
            "volume": close_volume,
            "close_price": price,
            "reason": reason,
            "pnl": pnl,
        }
        self.risk.log_trade(trade_record)

        return trade_record

    def modify_sl_tp(
        self,
        position: dict,
        new_sl: float | None = None,
        new_tp: float | None = None,
    ) -> bool:
        """Modify the stop loss and/or take profit of an open position."""
        symbol = position.get("symbol", "")
        ticket = position.get("ticket", 0)

        sym_info = self.mt5.symbol_info(symbol)
        if sym_info is None:
            return False
        digits = sym_info.get("digits", 5)

        current_sl = position.get("sl", 0)
        current_tp = position.get("tp", 0)

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": round(new_sl, digits) if new_sl is not None else current_sl,
            "tp": round(new_tp, digits) if new_tp is not None else current_tp,
            "magic": cfg.MAGIC_NUMBER,
        }

        result = self.mt5.send_order(request)
        if result is None or result.get("retcode") != mt5.TRADE_RETCODE_DONE:
            log.warning(f"Failed to modify SL/TP for {symbol} #{ticket}: {result}")
            return False

        log.info(f"Modified {symbol} #{ticket}: SL={request['sl']} TP={request['tp']}")
        return True
