from __future__ import annotations

from typing import Optional

import MetaTrader5 as mt5

from brokers.mt5 import MT5Broker
from config import settings as cfg
from utils.logger import get_logger

log = get_logger("oco_order_mgr")


class OcoOrderManager:
    def __init__(self, broker: MT5Broker):
        self.broker = broker
        self._unwind_pending: dict[str, bool] = {}

    def place_stop(self, order, current_price: float = 0.0) -> Optional[dict]:
        if not self.broker.select_symbol(order.symbol):
            return None

        sym_info = self.broker.symbol_info(order.symbol) or {}
        price = self.broker.normalize_price(order.symbol, float(order.price))
        volume = self.broker.normalize_volume(order.symbol, float(order.volume))
        if volume <= 0:
            return None

        if current_price > 0 and not self.broker.price_meets_stops_level(order.symbol, price, current_price):
            return None

        side = str(order.side).upper()
        if side == "BUY":
            order_type = mt5.ORDER_TYPE_BUY_STOP
        else:
            order_type = mt5.ORDER_TYPE_SELL_STOP

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": order.symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "magic": cfg.MAGIC_NUMBER,
            "comment": str(order.comment),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self.broker.pick_filling_mode(sym_info),
        }

        check = self.broker.check_order(request)
        if check is None or check.get("retcode", 0) != 0:
            return None
        return self.broker.send_order(request)

    def cancel(self, ticket: int, reason: str = "") -> Optional[dict]:
        result = self.broker.cancel_order(ticket)
        if result and result.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010):
            log.info(f"cancelled ticket={ticket} reason={reason}")
        return result

    def close_position_by_ticket(
        self,
        symbol: str,
        position_ticket: int,
        volume: float,
        side: str,
        reason: str = "",
    ) -> bool:
        result = self.broker.close_position(position_ticket, symbol, volume, side)
        if result and result.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010):
            log.info(
                f"{symbol}: closed position #{position_ticket} "
                f"{side} {volume} lots ({reason})"
            )
            return True
        return False

    def unwind_position(self, symbol: str, inventory_lots: float) -> bool:
        if abs(inventory_lots) < 1e-8:
            self._unwind_pending.pop(symbol, None)
            return False

        if self._unwind_pending.get(symbol):
            positions = self.broker.our_positions(symbol)
            current_lots = sum(
                (1 if p.get("type", 0) == mt5.POSITION_TYPE_BUY else -1) * p.get("volume", 0.0)
                for p in positions
            )
            if abs(current_lots) >= abs(inventory_lots) * 0.5:
                return False
            self._unwind_pending[symbol] = False

        positions = self.broker.our_positions(symbol)
        if not positions:
            self._unwind_pending.pop(symbol, None)
            return False

        sent_any = False
        for pos in positions:
            ticket = pos.get("ticket", 0)
            volume = pos.get("volume", 0.0)
            ptype = pos.get("type", mt5.POSITION_TYPE_BUY)
            if not ticket or volume <= 0:
                continue
            close_side = "SELL" if ptype == mt5.POSITION_TYPE_BUY else "BUY"
            if self.close_position_by_ticket(
                symbol=symbol,
                position_ticket=int(ticket),
                volume=float(volume),
                side=close_side,
                reason="risk_unwind",
            ):
                sent_any = True

        if sent_any:
            self._unwind_pending[symbol] = True
        return sent_any
