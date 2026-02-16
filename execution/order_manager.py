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
    deal_entry: int = 0   # 0=DEAL_ENTRY_IN (new position), 1=DEAL_ENTRY_OUT (close)
    position_ticket: int = 0  # MT5 position ticket
    deal_id: int = 0  # MT5 deal ticket (for de-dup)


class TradeJournal:
    """Append-only JSONL trade journal for auditability."""

    def __init__(self, path: Path | None = None):
        self.path = path or cfg.TRADE_JOURNAL_PATH
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.warning(f"Trade journal disabled (cannot create dir): {exc}")
            self.path = None

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
        if self.path is None:
            return
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
        """Select the filling mode for orders.

        OANDA reports filling_mode=1 which should be IOC.
        The bitmask interpretation varies by broker, so we use a
        practical approach: try IOC first (works on most brokers
        including OANDA), then RETURN, then FOK as last resort.
        """
        return self.broker.pick_filling_mode(sym_info)

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
            retcode = check.get("retcode", 0) if check else -1
            # 10015 = Invalid price (too close to market) — don't spam warnings
            if retcode != 10015:
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

    def set_exit_tp(self, symbol: str, position_ticket: int, tp_price: float) -> bool:
        """Set take-profit on a position. Returns True on success.

        This is how exits work on hedging accounts: instead of a separate
        sell limit order, we set TP directly on the position. MT5 closes
        it automatically when price hits the TP.
        """
        result = self.broker.modify_position_tp(
            position_ticket, symbol, sl=0.0, tp=tp_price
        )
        retcode = result.get("retcode") if result else None
        if retcode in (mt5.TRADE_RETCODE_DONE, 10010):
            log.info(
                f"{symbol}: SET TP on position #{position_ticket} "
                f"at {tp_price:.5f}"
            )
            return True
        if retcode == 10025:
            log.info(
                f"{symbol}: TP already set on position #{position_ticket} "
                f"at {tp_price:.5f}"
            )
            return True
        log.warning(
            f"{symbol}: failed to set TP on position #{position_ticket} "
            f"tp={tp_price:.5f} result={result}"
        )
        return False

    def close_position_by_ticket(
        self, symbol: str, position_ticket: int, volume: float, side: str,
        reason: str = "",
    ) -> bool:
        """Close a specific position by ticket. Returns True on success.

        Used for unwind (risk halt) and weekend close. The `side` is the
        CLOSE direction (SELL to close a long, BUY to close a short).
        """
        result = self.broker.close_position(
            position_ticket, symbol, volume, side
        )
        if result and result.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010):
            log.info(
                f"{symbol}: CLOSED position #{position_ticket} "
                f"{side} {volume} lots ({reason})"
            )
            self.journal.log_safety(
                "POSITION_CLOSED",
                f"{symbol}: #{position_ticket} {side} {volume} lots ({reason})"
            )
            return True
        log.warning(
            f"{symbol}: failed to close position #{position_ticket} "
            f"result={result}"
        )
        return False

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

        # Close each position by ticket (hedging-safe).
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
                symbol, ticket, volume, close_side, reason="grid_unwind"
            ):
                sent_any = True

        if sent_any:
            self._unwind_pending[symbol] = True
            self.journal.log_safety(
                "UNWIND", f"{symbol}: close all positions by ticket"
            )
        return sent_any

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
                ticket = order.get("ticket", 0)
                log.info(
                    f"{symbol}: CANCEL stale #{ticket} "
                    f"comment={comment} price={order.get('price_open', 0):.5f}"
                )
                self.cancel(ticket, reason="stale")
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
                vol_diff = abs(vol_existing - order.volume)
                # Volume tolerance: 0.015 lots (1.5 lot steps) — prevents
                # churn from size_for_rung fluctuations due to inv_ratio changes
                if (
                    price_diff <= requote_ticks * tick_size
                    and vol_diff < 0.015
                ):
                    continue
                # Cancel and replace — log the reason
                ticket = existing_order.get("ticket", 0)
                log.info(
                    f"{symbol}: REPLACE #{ticket} {order.comment} "
                    f"price {price_existing:.5f}->{order.price:.5f} "
                    f"(diff={price_diff:.5f}, thresh={requote_ticks * tick_size:.5f}) "
                    f"vol {vol_existing:.2f}->{order.volume:.2f} "
                    f"(diff={vol_diff:.4f})"
                )
                self.cancel(ticket, reason="requote")
                actual_pending -= 1

            if actual_pending >= pending_cap:
                log.warning(
                    f"{symbol}: max pending orders ({pending_cap}) reached — "
                    f"skipping {order.comment}"
                )
                continue

            result = self.place_limit(order, current_price=current_price)
            if result and result.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010):
                log.info(
                    f"{symbol}: PLACED {order.side} {order.volume:.2f} "
                    f"at {order.price:.5f} [{order.comment}]"
                )
                actual_pending += 1

    def detect_fills(
        self,
        symbol: str,
        since_iso: str,
        grid_id: str | None = None,
        since_deal_id: int = 0,
    ) -> list[FillEvent]:
        """Return fill events since the given timestamp.

        Handles two types of deals:
        - DEAL_ENTRY_IN (0): Entry fill — a limit order filled, position opened.
          The engine should toggle rung ENTRY->EXIT and set TP on the position.
        - DEAL_ENTRY_OUT (1): TP/close fill — position closed at TP.
          The engine should toggle rung EXIT->ENTRY and place a new entry order.

        Deals with comments that don't match our grid format (unwind, weekend
        close, manual trades) are skipped.
        """
        try:
            since_dt = datetime.fromisoformat(since_iso)
        except Exception:
            log.warning(f"Bad last_deal_time '{since_iso}', falling back to start-of-day")
            since_dt = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        deals = self.broker.history_deals(since_dt)
        fills: list[FillEvent] = []
        # Process in chronological order for deterministic de-dup
        deals_sorted = sorted(
            deals,
            key=lambda x: (
                x.get("time_msc", x.get("time", 0)),
                x.get("ticket", x.get("deal", 0)),
            ),
        )
        for d in deals_sorted:
            if d.get("symbol") != symbol:
                continue

            deal_entry = d.get("entry", -1)
            # Only process IN (new fills) and OUT (TP closes)
            if deal_entry not in (0, 1):
                continue

            deal_id = int(d.get("ticket") or d.get("deal") or 0)
            if deal_id and since_deal_id and deal_id <= since_deal_id:
                continue

            comment = d.get("comment", "")
            # Skip non-grid deals (unwind, weekend close, manual)
            parsed = parse_comment(comment)
            if not parsed:
                # MT5 may return empty comments on deals.
                # For OUT deals, we still emit a fill to match by position_id.
                # For IN deals, we also emit a fill so the engine can adopt
                # the position and attach TP.
                if deal_entry in (0, 1):
                    # Try to find the rung by matching the position ticket
                    # against our tracked positions. The engine will handle
                    # matching by position_ticket.
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
                        rung_id="",  # unknown — engine matches by position_ticket
                        state="ENTRY" if deal_entry == 0 else "EXIT",
                        deal_entry=deal_entry,
                        position_ticket=d.get("position_id", 0),
                        deal_id=deal_id,
                    )
                    fills.append(fill)
                continue

            parsed_grid_id, rung_id, state = parsed
            if grid_id and parsed_grid_id != grid_id:
                continue
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
                deal_entry=deal_entry,
                position_ticket=d.get("position_id", 0),
                deal_id=deal_id,
            )
            fills.append(fill)
        return fills
