from __future__ import annotations

import MetaTrader5 as mt5

from brokers.mt5 import MT5Broker
from config import settings as cfg
from execution.models import OrderIntent
from utils.logger import get_logger

log = get_logger("order_manager")


class OrderManager:
    def __init__(self, broker: MT5Broker) -> None:
        self.broker = broker

    def sync_pending(self, symbol: str, intents: list[OrderIntent], max_pending: int) -> None:
        existing = self.broker.our_pending_orders(symbol)
        for order in existing:
            self.broker.cancel_order(int(order["ticket"]))

        for intent in intents[:max_pending]:
            self.place_intent(intent)

    def place_intent(self, intent: OrderIntent) -> None:
        info = self.broker.symbol_info(intent.symbol)
        if not info:
            return
        volume = self.broker.normalize_volume(info, intent.volume)
        price = self.broker.normalize_price(info, intent.price)
        tp = self.broker.normalize_price(info, intent.tp) if intent.tp else 0.0

        if intent.order_type == "LIMIT":
            order_type = mt5.ORDER_TYPE_BUY_LIMIT if intent.side == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
            req = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": intent.symbol,
                "type": order_type,
                "volume": volume,
                "price": price,
                "tp": tp,
                "deviation": 10,
                "magic": cfg.MAGIC_NUMBER,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN,
                "comment": intent.comment or "phased_grid",
            }
        else:
            order_type = mt5.ORDER_TYPE_BUY if intent.side == "BUY" else mt5.ORDER_TYPE_SELL
            tick = self.broker.symbol_tick(intent.symbol)
            if not tick:
                return
            deal_price = float(tick["ask"] if intent.side == "BUY" else tick["bid"])
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": intent.symbol,
                "type": order_type,
                "volume": volume,
                "price": deal_price,
                "deviation": 20,
                "magic": cfg.MAGIC_NUMBER,
                "type_filling": mt5.ORDER_FILLING_IOC,
                "comment": intent.comment or "phased_grid",
            }
            if intent.position_ticket:
                req["position"] = int(intent.position_ticket)

        result = self.broker.send_order(req)
        if not result:
            return
        code = int(result.get("retcode", 0))
        if code not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            log.warning(
                f"Order rejected {intent.symbol} {intent.side} {intent.order_type} retcode={code}"
            )

    def close_positions(self, symbol: str, side: str, max_count: int) -> int:
        positions = self.broker.our_positions(symbol)
        closed = 0
        for pos in positions:
            if closed >= max_count:
                break
            pos_is_buy = int(pos.get("type", 0)) == mt5.POSITION_TYPE_BUY
            if side == "BUY" and not pos_is_buy:
                continue
            if side == "SELL" and pos_is_buy:
                continue

            close_side = "SELL" if pos_is_buy else "BUY"
            intent = OrderIntent(
                symbol=symbol,
                side=close_side,
                order_type="MARKET",
                volume=float(pos.get("volume", 0.0)),
                price=0.0,
                comment="phased_grid_cleanup",
                position_ticket=int(pos.get("ticket", 0)),
            )
            self.place_intent(intent)
            closed += 1
        return closed
