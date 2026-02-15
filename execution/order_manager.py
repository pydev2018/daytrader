"""
Order reconciliation and fill detection for grid trading.

Fixes:
- S0 #5: sync_orders now tracks post-cancel count correctly.
- S1 #7: Orders are validated against stops_level before placement.
- Added trade journal logging for all fills and orders.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5

from brokers.mt5 import MT5Broker
from config import settings as cfg
from grid.orders import OrderSpec, parse_comment
from utils.logger import get_logger

log = get_logger("order_mgr")


@dataclass
class FillEvent:
    symbol: str
    side: str
    price: float
    volume: float
    order_ticket: int
    comment: str
    time_iso: str
    rung_id: str
    state: str


class TradeJournal:
    """Append-only JSONL trade journal for auditability."""

    def __init__(self, path: Path | None = None):
        self.path = path or cfg.TRADE_JOURNAL_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_fill(self, fill: FillEvent, equity: float = 0.0, inventory_lots: float = 0.0):
        record = {
            "event": "fill",
            "ts": datetime.now(timezone.utc).isoformat(),
            **asdict(fill),
            "equity": equity,
            "inventory_lots": inventory_lots,
        }
        self._append(record)

    def log_order_placed(self, spec: OrderSpec, ticket: int = 0):
        record = {
            "event": "order_placed",
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": spec.symbol,
            "side": spec.side,
            "price": spec.price,
            "volume": spec.volume,
            "comment": spec.comment,
            "rung_id": spec.rung_id,
            "state": spec.state,
            "ticket": ticket,
        }
        self._append(record)

    def log_order_cancelled(self, ticket: int, reason: str = ""):
        record = {
            "event": "order_cancelled",
            "ts": datetime.now(timezone.utc).isoformat(),
            "ticket": ticket,
            "reason": reason,
        }
        self._append(record)

    def log_safety(self, event_type: str, details: str):
        record = {
            "event": "safety",
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "details": details,
        }
        self._append(record)

    def _append(self, record: dict):
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            log.warning(f"Trade journal write failed: {exc}")


class OrderManager:
    def __init__(self, broker: MT5Broker):
        self.broker = broker
        self.journal = TradeJournal()
        self._unwind_pending: dict[str, bool] = {}  # symbol -> True if unwind in flight

    def _pick_filling(self, sym_info: dict) -> int:
        # Prefer RETURN when available
        if sym_info:
            fill_mode = sym_info.get("filling_mode", 0)
            if fill_mode & mt5.SYMBOL_FILLING_RETURN:
                return mt5.ORDER_FILLING_RETURN
            if fill_mode & mt5.SYMBOL_FILLING_IOC:
                return mt5.ORDER_FILLING_IOC
            if fill_mode & mt5.SYMBOL_FILLING_FOK:
                return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def place_limit(
        self, order: OrderSpec, current_price: float = 0.0
    ) -> Optional[dict]:
        if not self.broker.select_symbol(order.symbol):
            return None
        sym_info = self.broker.symbol_info(order.symbol) or {}
        price = self.broker.normalize_price(order.symbol, order.price)
        volume = self.broker.normalize_volume(order.symbol, order.volume)
        if volume <= 0:
            return None

        # S1 #7: Validate stops_level / freeze_level
        if current_price > 0:
            if not self.broker.price_meets_stops_level(order.symbol, price, current_price):
                min_dist = self.broker.get_min_distance(order.symbol)
                log.debug(
                    f"{order.symbol}: skipping {order.comment} — price {price} "
                    f"within stops_level ({min_dist:.5f}) of current {current_price}"
                )
                return None

        order_type = (
            mt5.ORDER_TYPE_BUY_LIMIT if order.side == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        )
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": order.symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "magic": cfg.MAGIC_NUMBER,
            "comment": order.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._pick_filling(sym_info),
        }
        check = self.broker.check_order(request)
        if check is None or check.get("retcode", 0) != 0:
            log.warning(f"{order.symbol}: order_check failed — {check}")
            return None
        result = self.broker.send_order(request)
        if result and result.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010):
            self.journal.log_order_placed(order, result.get("order", 0))
        return result

    def place_market(
        self, symbol: str, side: str, volume: float, comment: str
    ) -> Optional[dict]:
        if not self.broker.select_symbol(symbol):
            return None
        tick = self.broker.symbol_tick(symbol)
        if tick is None:
            return None
        price = tick["ask"] if side == "BUY" else tick["bid"]
        price = self.broker.normalize_price(symbol, price)
        volume = self.broker.normalize_volume(symbol, volume)
        if volume <= 0:
            return None
        order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
        sym_info = self.broker.symbol_info(symbol) or {}
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": cfg.MAGIC_NUMBER,
            "comment": comment,
            "type_filling": self._pick_filling(sym_info),
        }
        check = self.broker.check_order(request)
        if check is None or check.get("retcode", 0) != 0:
            log.warning(f"{symbol}: market order_check failed — {check}")
            return None
        return self.broker.send_order(request)

    def cancel(self, ticket: int, reason: str = ""):
        result = self.broker.cancel_order(ticket)
        if result and result.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010):
            self.journal.log_order_cancelled(ticket, reason)
        return result

    def unwind_position(self, symbol: str, inventory_lots: float) -> bool:
        """Market-close the position. Returns True if order was sent.

        Fix S0 #3: Tracks pending unwind to prevent spamming market orders.
        Only sends one unwind per symbol until it completes or fails.
        """
        if abs(inventory_lots) < 1e-8:
            self._unwind_pending.pop(symbol, None)
            return False

        if self._unwind_pending.get(symbol):
            # Check if prior unwind completed (position reduced)
            positions = self.broker.our_positions(symbol)
            current_lots = sum(
                (1 if p.get("type", 0) == mt5.POSITION_TYPE_BUY else -1) * p.get("volume", 0.0)
                for p in positions
            )
            if abs(current_lots) < abs(inventory_lots) * 0.5:
                # Position reduced significantly — allow another unwind if needed
                self._unwind_pending[symbol] = False
            else:
                log.info(f"{symbol}: unwind already pending, skipping duplicate market order")
                return False

        side = "SELL" if inventory_lots > 0 else "BUY"
        result = self.place_market(symbol, side, abs(inventory_lots), "grid_unwind")
        # C5: Check retcode for actual success, not just non-None result.
        # A failed market order (e.g., insufficient margin) must NOT set the
        # pending flag, otherwise future unwinds are permanently blocked.
        if result and result.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010):
            self._unwind_pending[symbol] = True
            self.journal.log_safety("UNWIND", f"{symbol}: {side} {abs(inventory_lots):.2f} lots")
            return True
        if result:
            log.warning(
                f"{symbol}: unwind market order failed: retcode={result.get('retcode')} "
                f"comment={result.get('comment')}"
            )
        return False

    def clear_unwind_flag(self, symbol: str):
        """Clear unwind tracking (call when halt clears)."""
        self._unwind_pending.pop(symbol, None)

    def sync_orders(
        self,
        symbol: str,
        desired: list[OrderSpec],
        grid_id: str,
        tick_size: float,
        requote_ticks: int,
        max_pending: int | None = None,
        current_price: float = 0.0,
    ):
        """Reconcile broker pending orders with desired orders.

        Fix S0 #5: Tracks actual pending count after cancellations.
        """
        existing = self.broker.pending_orders(symbol)
        prefix = f"g{grid_id}:"

        existing_by_comment: dict[str, dict] = {
            o.get("comment", ""): o
            for o in existing
            if o.get("comment", "").startswith(prefix)
        }

        desired_comments = {o.comment for o in desired}

        # Cancel stale orders and track actual count
        cancelled_count = 0
        for comment, order in existing_by_comment.items():
            if comment not in desired_comments:
                self.cancel(order.get("ticket", 0), reason="stale")
                cancelled_count += 1

        # S0 #5: Actual pending count = total existing - our cancelled
        actual_pending = len(existing) - cancelled_count

        # Place or adjust desired
        pending_cap = max_pending or cfg.MAX_PENDING_ORDERS_PER_SYMBOL
        for order in desired:
            existing_order = existing_by_comment.get(order.comment)
            if existing_order:
                price_existing = existing_order.get("price_open", 0.0)
                vol_existing = existing_order.get("volume_current", 0.0)
                price_diff = abs(price_existing - order.price)
                if (
                    price_diff <= requote_ticks * tick_size
                    and abs(vol_existing - order.volume) < 1e-6
                ):
                    continue
                # Cancel and replace (simpler + safer than modify)
                self.cancel(existing_order.get("ticket", 0), reason="requote")
                actual_pending -= 1

            if actual_pending >= pending_cap:
                log.warning(
                    f"{symbol}: max pending orders ({pending_cap}) reached — "
                    f"skipping {order.comment}"
                )
                continue

            result = self.place_limit(order, current_price=current_price)
            if result and result.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010):
                actual_pending += 1

    def detect_fills(self, symbol: str, since_iso: str) -> list[FillEvent]:
        """Return fill events since the given timestamp.

        Fix C2: Only process DEAL_ENTRY_IN deals. Close/unwind deals
        (DEAL_ENTRY_OUT) are skipped to prevent the grid from
        misinterpreting a position close as a new entry fill.

        Fix H2: On ISO parse failure, fall back to start-of-day
        instead of now() to avoid silently missing fills.
        """
        try:
            since_dt = datetime.fromisoformat(since_iso)
        except Exception:
            # H2: Fall back to start of day, not now (which returns zero deals)
            log.warning(f"Bad last_deal_time '{since_iso}', falling back to start-of-day")
            since_dt = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        deals = self.broker.history_deals(since_dt)
        fills: list[FillEvent] = []
        for d in deals:
            if d.get("symbol") != symbol:
                continue

            # C2: Only process entry deals (new fills into positions).
            # Skip DEAL_ENTRY_OUT (position closes from unwind/SL/TP).
            # DEAL_ENTRY_IN = 0, DEAL_ENTRY_OUT = 1
            deal_entry = d.get("entry", -1)
            if deal_entry != 0:  # 0 = DEAL_ENTRY_IN
                continue

            comment = d.get("comment", "")
            parsed = parse_comment(comment)
            if not parsed:
                continue
            _, rung_id, state = parsed
            deal_time = datetime.fromtimestamp(
                d.get("time", 0), tz=timezone.utc
            ).isoformat()
            side = "BUY" if d.get("type", 0) == mt5.ORDER_TYPE_BUY else "SELL"
            fill = FillEvent(
                symbol=symbol,
                side=side,
                price=d.get("price", 0.0),
                volume=d.get("volume", 0.0),
                order_ticket=d.get("order", 0),
                comment=comment,
                time_iso=deal_time,
                rung_id=rung_id,
                state=state,
            )
            fills.append(fill)
        return fills
