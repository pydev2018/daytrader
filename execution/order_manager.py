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
        active_intents = intents[:max_pending]

        def get_mt5_type(intent: OrderIntent) -> int:
            if intent.order_type == "LIMIT":
                return mt5.ORDER_TYPE_BUY_LIMIT if intent.side == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
            elif intent.order_type == "STOP":
                return mt5.ORDER_TYPE_BUY_STOP if intent.side == "BUY" else mt5.ORDER_TYPE_SELL_STOP
            return -1

        unmatched_intents = list(active_intents)
        orders_to_cancel = []

        for order in existing:
            order_type = int(order.get("type", -1))
            order_price = float(order.get("price_open", 0.0))
            order_vol = float(order.get("volume_initial", 0.0))
            
            matched_idx = -1
            for i, intent in enumerate(unmatched_intents):
                intent_type = get_mt5_type(intent)
                # Use a small epsilon for float comparison
                if (intent_type == order_type and 
                    abs(intent.price - order_price) < 1e-5 and 
                    abs(intent.volume - order_vol) < 1e-5):
                    matched_idx = i
                    break
            
            if matched_idx >= 0:
                # Match found! Keep this order, remove intent from unmatched
                unmatched_intents.pop(matched_idx)
            else:
                # No matching intent found, this order needs to be cancelled
                orders_to_cancel.append(int(order["ticket"]))

        # Cancel unmatched existing orders
        for ticket in orders_to_cancel:
            self.broker.cancel_order(ticket)

        # Place remaining unmatched intents
        for intent in unmatched_intents:
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
        elif intent.order_type == "STOP":
            order_type = mt5.ORDER_TYPE_BUY_STOP if intent.side == "BUY" else mt5.ORDER_TYPE_SELL_STOP
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

    def close_positions(
        self,
        symbol: str,
        side: str,
        max_count: int,
        max_loss_budget: float | None = None,
    ) -> tuple[int, float]:
        positions = self.broker.our_positions(symbol)
        positions = sorted(
            positions,
            key=lambda p: abs(min(float(p.get("profit", 0.0)), 0.0)),
        )
        closed = 0
        est_realized = 0.0
        remaining_budget = max_loss_budget
        for pos in positions:
            if closed >= max_count:
                break
            pos_is_buy = int(pos.get("type", 0)) == mt5.POSITION_TYPE_BUY
            if side == "BUY" and not pos_is_buy:
                continue
            if side == "SELL" and pos_is_buy:
                continue

            est_pnl = float(pos.get("profit", 0.0))
            if remaining_budget is not None and est_pnl < 0 and abs(est_pnl) > remaining_budget:
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
            est_realized += est_pnl
            if remaining_budget is not None and est_pnl < 0:
                remaining_budget = max(0.0, remaining_budget - abs(est_pnl))
        return closed, est_realized

    def unwind_positions(
        self,
        symbol: str,
        max_count: int,
        max_loss_budget: float | None = None,
    ) -> tuple[int, float]:
        positions = self.broker.our_positions(symbol)
        if not positions:
            return 0, 0.0

        ranked = sorted(positions, key=lambda p: float(p.get("profit", 0.0)))
        closed = 0
        est_realized = 0.0
        remaining_budget = max_loss_budget
        for pos in ranked:
            if closed >= max_count:
                break

            est_pnl = float(pos.get("profit", 0.0))
            if remaining_budget is not None and est_pnl < 0 and abs(est_pnl) > remaining_budget:
                continue

            pos_is_buy = int(pos.get("type", 0)) == mt5.POSITION_TYPE_BUY
            close_side = "SELL" if pos_is_buy else "BUY"
            intent = OrderIntent(
                symbol=symbol,
                side=close_side,
                order_type="MARKET",
                volume=float(pos.get("volume", 0.0)),
                price=0.0,
                comment="phased_grid_risk_off_unwind",
                position_ticket=int(pos.get("ticket", 0)),
            )
            self.place_intent(intent)
            closed += 1
            est_realized += est_pnl
            if remaining_budget is not None and est_pnl < 0:
                remaining_budget = max(0.0, remaining_budget - abs(est_pnl))
        return closed, est_realized
