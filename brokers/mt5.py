"""
===============================================================================
  MT5 Broker Adapter — connectivity, data access, order operations
===============================================================================
Fixes:
- S2 #17: normalize_volume uses vol_step precision instead of hardcoded 2dp.
- S1 #7: Added stops_level/freeze_level validation helpers.
- S1 #11: ensure_connected uses exponential backoff with jitter.
"""

from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5
import pandas as pd

from config import settings as cfg
from utils.logger import get_logger

log = get_logger("mt5")

# ─── MT5 timeframe mapping ──────────────────────────────────────────────────
TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M5": mt5.TIMEFRAME_M5,
    "M10": mt5.TIMEFRAME_M10,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}

# ─── Retriable MT5 return codes ──────────────────────────────────────────────
RETRIABLE_RETCODES = {
    mt5.TRADE_RETCODE_REQUOTE,      # 10004 — requote
    mt5.TRADE_RETCODE_TIMEOUT,      # 10012 — timeout
    mt5.TRADE_RETCODE_PRICE_OFF,    # 10021 — prices changed
    mt5.TRADE_RETCODE_CONNECTION,   # 10031 — connection lost
    # H7: Removed 10006 (TRADE_RETCODE_REJECT) — explicit rejections
    # should NOT be retried (could be invalid params, margin, broker block).
}


class MT5Broker:
    """Manages MT5 connection and provides trading helpers."""

    def __init__(self):
        self._connected = False
        self._sym_info_cache: dict[str, dict] = {}
        self._cache_ts: dict[str, float] = {}
        self._cache_ttl = 5.0  # seconds

    # =====================================================================
    #  CONNECTION
    # =====================================================================

    def connect(self) -> bool:
        """Initialise MT5 terminal connection."""
        kwargs: dict = {}
        if cfg.MT5_PATH:
            kwargs["path"] = cfg.MT5_PATH
        if cfg.MT5_LOGIN:
            kwargs["login"] = cfg.MT5_LOGIN
        if cfg.MT5_PASSWORD:
            kwargs["password"] = cfg.MT5_PASSWORD
        if cfg.MT5_SERVER:
            kwargs["server"] = cfg.MT5_SERVER
        kwargs["timeout"] = cfg.MT5_TIMEOUT

        if not mt5.initialize(**kwargs):
            err = mt5.last_error()
            log.error(f"MT5 initialize failed: {err}")
            return False

        info = mt5.terminal_info()
        acc = mt5.account_info()
        if info is None or acc is None:
            log.error("MT5 initialized but terminal_info or account_info is None")
            mt5.shutdown()
            return False
        log.info(
            f"MT5 connected  | terminal={info.name}  build={info.build}  "
            f"account={acc.login}  server={acc.server}  "
            f"balance={acc.balance:.2f} {acc.currency}  "
            f"leverage=1:{acc.leverage}"
        )
        self._connected = True
        self._sym_info_cache.clear()
        return True

    def disconnect(self):
        """Shut down MT5 connection."""
        mt5.shutdown()
        self._connected = False
        log.info("MT5 disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected and mt5.terminal_info() is not None

    def ensure_connected(self):
        """Reconnect if connection dropped; raises after retries.

        Uses exponential backoff with jitter to avoid thundering herd.
        """
        if self.is_connected:
            return
        log.warning("MT5 connection lost — reconnecting ...")
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            mt5.shutdown()
            if self.connect():
                log.info(f"MT5 reconnected on attempt {attempt}")
                return
            # Exponential backoff with jitter: 2^attempt + random(0,1)
            delay = min(2 ** attempt + random.random(), 30.0)
            log.warning(f"MT5 reconnect attempt {attempt}/{max_attempts} failed, "
                       f"retrying in {delay:.1f}s")
            time.sleep(delay)
        log.error(f"MT5 reconnect failed after {max_attempts} attempts")
        raise ConnectionError("MT5 connection lost and could not be restored")

    # =====================================================================
    #  ACCOUNT
    # =====================================================================

    def account_info(self) -> dict:
        self.ensure_connected()
        info = mt5.account_info()
        return info._asdict() if info else {}

    def account_balance(self) -> float:
        acc = self.account_info()
        return acc.get("balance", 0.0)

    def account_equity(self) -> float:
        acc = self.account_info()
        return acc.get("equity", 0.0)

    def account_model(self) -> dict:
        """Return account model flags (netting/hedging/FIFO)."""
        acc = self.account_info()
        margin_mode = acc.get("margin_mode", None)
        fifo_close = bool(acc.get("fifo_close", False))
        is_hedging = margin_mode == getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", -1)
        is_netting = margin_mode == getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_NETTING", -1)
        return {
            "margin_mode": margin_mode,
            "fifo_close": fifo_close,
            "is_hedging": is_hedging,
            "is_netting": is_netting,
            "limit_orders": acc.get("limit_orders", 0),
            "trade_mode": acc.get("trade_mode", 0),
        }

    # =====================================================================
    #  SYMBOLS / MARKET DATA
    # =====================================================================

    def select_symbol(self, symbol: str) -> bool:
        self.ensure_connected()
        info = mt5.symbol_info(symbol)
        if info is None:
            return False
        if not info.visible:
            if not mt5.symbol_select(symbol, True):
                log.warning(f"Cannot select {symbol} in MarketWatch")
                return False
        return True

    def symbol_info(self, symbol: str) -> Optional[dict]:
        """Get symbol info with short-lived cache to reduce MT5 calls."""
        now = time.time()
        if symbol in self._sym_info_cache:
            if now - self._cache_ts.get(symbol, 0) < self._cache_ttl:
                return self._sym_info_cache[symbol]
        self.ensure_connected()
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        d = info._asdict()
        self._sym_info_cache[symbol] = d
        self._cache_ts[symbol] = now
        return d

    def symbol_tick(self, symbol: str) -> Optional[dict]:
        self.ensure_connected()
        tick = mt5.symbol_info_tick(symbol)
        return tick._asdict() if tick else None

    def get_rates(self, symbol: str, timeframe: str, count: int) -> Optional[pd.DataFrame]:
        self.ensure_connected()
        tf = TF_MAP.get(timeframe, mt5.TIMEFRAME_M1)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    # =====================================================================
    #  POSITIONS / ORDERS
    # =====================================================================

    def open_positions(self, symbol: str | None = None) -> list[dict]:
        self.ensure_connected()
        if symbol:
            pos = mt5.positions_get(symbol=symbol)
        else:
            pos = mt5.positions_get()
        if pos is None:
            return []
        return [p._asdict() for p in pos]

    def our_positions(self, symbol: str | None = None) -> list[dict]:
        positions = self.open_positions(symbol)
        return [p for p in positions if p.get("magic") == cfg.MAGIC_NUMBER]

    def pending_orders(self, symbol: str | None = None) -> list[dict]:
        self.ensure_connected()
        if symbol:
            orders = mt5.orders_get(symbol=symbol)
        else:
            orders = mt5.orders_get()
        if orders is None:
            return []
        return [o._asdict() for o in orders if o.magic == cfg.MAGIC_NUMBER]

    def history_deals(
        self, from_date: datetime, to_date: datetime | None = None
    ) -> list[dict]:
        self.ensure_connected()
        to_date = to_date or datetime.now(timezone.utc)
        deals = mt5.history_deals_get(from_date, to_date)
        if deals is None:
            return []
        return [d._asdict() for d in deals if d.magic == cfg.MAGIC_NUMBER]

    # =====================================================================
    #  ORDER OPERATIONS
    # =====================================================================

    def pick_filling_mode(self, sym_info: dict | None) -> int:
        """Pick a supported filling mode for the symbol.

        Some brokers expose a single mode via `filling_mode` (enum),
        others expose a bitmask in `trade_fill_flags`. We prefer IOC,
        then RETURN, then FOK as a last resort.
        """
        if not sym_info:
            return mt5.ORDER_FILLING_IOC

        flags = sym_info.get("trade_fill_flags") or 0
        if isinstance(flags, int):
            # Bitmask: 1=FOK, 2=IOC, 4=RETURN
            if flags & 2:
                return mt5.ORDER_FILLING_IOC
            if flags & 4:
                return mt5.ORDER_FILLING_RETURN
            if flags & 1:
                return mt5.ORDER_FILLING_FOK

        # Fallback: prefer IOC for OANDA/FX, then RETURN.
        return mt5.ORDER_FILLING_IOC

    def check_order(self, request: dict) -> Optional[dict]:
        self.ensure_connected()
        result = mt5.order_check(request)
        if result is None:
            log.error(f"order_check returned None: {mt5.last_error()}")
            return None
        return result._asdict()

    def send_order(self, request: dict, max_retries: int = 2) -> Optional[dict]:
        """Send order with retry on retriable errors.

        Uses exponential backoff with jitter for retriable return codes.
        """
        self.ensure_connected()
        invalid_fill = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)
        for attempt in range(1 + max_retries):
            result = mt5.order_send(request)
            if result is None:
                log.error(f"order_send returned None: {mt5.last_error()}")
                return None
            rd = result._asdict()
            retcode = rd.get("retcode", 0)

            if retcode in (mt5.TRADE_RETCODE_DONE, 10010):
                return rd
            # "No changes" (already set). Treat as success for modify/SLTP.
            if retcode == 10025 and request.get("action") in (
                mt5.TRADE_ACTION_SLTP,
                mt5.TRADE_ACTION_MODIFY,
            ):
                return rd

            # If filling mode is unsupported, retry with alternatives.
            if retcode == invalid_fill and "type_filling" in request:
                tried = {request.get("type_filling")}
                for alt in (
                    mt5.ORDER_FILLING_IOC,
                    mt5.ORDER_FILLING_RETURN,
                    mt5.ORDER_FILLING_FOK,
                ):
                    if alt in tried:
                        continue
                    req2 = dict(request)
                    req2["type_filling"] = alt
                    result2 = mt5.order_send(req2)
                    if result2 is None:
                        continue
                    rd2 = result2._asdict()
                    if rd2.get("retcode") in (mt5.TRADE_RETCODE_DONE, 10010):
                        return rd2
                    tried.add(alt)

            if retcode in RETRIABLE_RETCODES and attempt < max_retries:
                delay = 0.5 * (2 ** attempt) + random.random() * 0.2
                log.warning(
                    f"order_send retriable error: retcode={retcode} "
                    f"comment={rd.get('comment')} — retry {attempt+1} in {delay:.1f}s"
                )
                time.sleep(delay)
                # Refresh price for market orders
                if request.get("action") == mt5.TRADE_ACTION_DEAL:
                    tick = mt5.symbol_info_tick(request.get("symbol", ""))
                    if tick:
                        if request.get("type") in (mt5.ORDER_TYPE_BUY,):
                            request["price"] = tick.ask
                        else:
                            request["price"] = tick.bid
                continue

            log.warning(
                f"order_send non-success: retcode={retcode} "
                f"comment={rd.get('comment')} symbol={request.get('symbol')}"
            )
            return rd
        return None

    def cancel_order(self, ticket: int) -> Optional[dict]:
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
            "magic": cfg.MAGIC_NUMBER,
        }
        return self.send_order(request, max_retries=1)

    def modify_order(
        self, ticket: int, price: float, sl: float = 0.0, tp: float = 0.0
    ) -> Optional[dict]:
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "price": price,
            "sl": sl,
            "tp": tp,
            "magic": cfg.MAGIC_NUMBER,
        }
        return self.send_order(request, max_retries=1)

    def modify_position_tp(
        self, position_ticket: int, symbol: str, sl: float = 0.0, tp: float = 0.0
    ) -> Optional[dict]:
        """Set SL/TP on an open position using TRADE_ACTION_SLTP.

        This modifies the position itself, not a pending order.
        On hedging accounts, this is how you set take-profit on a specific position.
        """
        tp = self.normalize_price(symbol, tp)
        sl = self.normalize_price(symbol, sl) if sl > 0 else 0.0
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": position_ticket,
            "symbol": symbol,
            "sl": sl,
            "tp": tp,
            "magic": cfg.MAGIC_NUMBER,
        }
        return self.send_order(request, max_retries=2)

    def close_position(
        self, position_ticket: int, symbol: str, volume: float, side: str
    ) -> Optional[dict]:
        """Close a specific position by ticket on a hedging account.

        Sends a TRADE_ACTION_DEAL with the position field set,
        which tells MT5 to close THAT specific position rather than
        opening a new one.
        """
        self.ensure_connected()
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None

        if side == "BUY":
            # Closing a short: buy at ask
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            # Closing a long: sell at bid
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid

        price = self.normalize_price(symbol, price)
        volume = self.normalize_volume(symbol, volume)
        if volume <= 0:
            return None

        sym_info = self.symbol_info(symbol) or {}
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": position_ticket,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": cfg.MAGIC_NUMBER,
            "comment": "grid_close",
            "type_filling": self.pick_filling_mode(sym_info),
        }
        return self.send_order(request, max_retries=2)

    def calc_margin(
        self, action: int, symbol: str, volume: float, price: float
    ) -> Optional[float]:
        self.ensure_connected()
        return mt5.order_calc_margin(action, symbol, volume, price)

    def calc_profit(
        self, action: int, symbol: str, volume: float,
        price_open: float, price_close: float,
    ) -> Optional[float]:
        self.ensure_connected()
        return mt5.order_calc_profit(action, symbol, volume, price_open, price_close)

    # =====================================================================
    #  NORMALIZATION & VALIDATION HELPERS
    # =====================================================================

    def normalize_price(self, symbol: str, price: float) -> float:
        info = self.symbol_info(symbol)
        if info is None:
            return price
        digits = info.get("digits", 5)
        return round(price, digits)

    def normalize_volume(self, symbol: str, volume: float) -> float:
        """Normalize volume to broker constraints.

        Fix S2 #17: Uses vol_step precision instead of hardcoded round(vol, 2).
        """
        info = self.symbol_info(symbol)
        if info is None:
            return volume
        vol_min = info.get("volume_min", 0.01)
        vol_max = info.get("volume_max", 100.0)
        vol_step = info.get("volume_step", 0.01)
        vol = max(vol_min, min(vol_max, volume))
        if vol_step > 0:
            # Floor to nearest step (don't round up to avoid over-sizing)
            vol = math.floor(vol / vol_step) * vol_step
        # Use step precision for rounding
        if vol_step > 0:
            step_decimals = max(0, -math.floor(math.log10(vol_step)))
        else:
            step_decimals = 2
        return round(vol, step_decimals)

    def price_meets_stops_level(
        self, symbol: str, order_price: float, current_price: float
    ) -> bool:
        """Check if order_price respects the broker's stops_level constraint.

        MT5 rejects limit orders placed within stops_level points of the
        current price. This method returns True if the order price is
        far enough away.
        """
        info = self.symbol_info(symbol)
        if info is None:
            return True  # can't check — let broker reject
        stops_level = info.get("trade_stops_level", 0)
        freeze_level = info.get("trade_freeze_level", 0)
        point = info.get("point", 0.00001)

        min_distance = max(stops_level, freeze_level) * point
        return abs(order_price - current_price) >= min_distance

    def get_min_distance(self, symbol: str) -> float:
        """Return minimum order distance from current price in price units."""
        info = self.symbol_info(symbol)
        if info is None:
            return 0.0
        stops_level = info.get("trade_stops_level", 0)
        freeze_level = info.get("trade_freeze_level", 0)
        point = info.get("point", 0.00001)
        return max(stops_level, freeze_level) * point
