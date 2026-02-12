"""
===============================================================================
  MT5 Connector — connection management, symbol discovery, data retrieval
===============================================================================
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

import config as cfg
from utils.logger import get_logger

log = get_logger("mt5")

# ─── MT5 timeframe mapping ──────────────────────────────────────────────────
TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M2":  mt5.TIMEFRAME_M2,
    "M3":  mt5.TIMEFRAME_M3,
    "M5":  mt5.TIMEFRAME_M5,
    "M10": mt5.TIMEFRAME_M10,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H2":  mt5.TIMEFRAME_H2,
    "H4":  mt5.TIMEFRAME_H4,
    "H6":  mt5.TIMEFRAME_H6,
    "H8":  mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1":  mt5.TIMEFRAME_D1,
    "W1":  mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


class MT5Connector:
    """Manages the MT5 terminal connection and provides data-access helpers."""

    def __init__(self):
        self._connected = False

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
            f"MT5 connected  │ terminal={info.name}  build={info.build}  "
            f"account={acc.login}  server={acc.server}  "
            f"balance={acc.balance:.2f} {acc.currency}  "
            f"leverage=1:{acc.leverage}"
        )
        self._connected = True
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
        """Reconnect if the connection was dropped, with retry.

        Raises ConnectionError after 3 failed attempts so callers cannot
        silently proceed on a dead connection (which would make
        our_positions() return [] and bypass risk limits).
        """
        if self.is_connected:
            return
        log.warning("MT5 connection lost — reconnecting …")
        for attempt in range(1, 4):
            mt5.shutdown()  # clean stale IPC state before retry
            if self.connect():
                log.info(f"MT5 reconnected on attempt {attempt}")
                return
            log.warning(f"MT5 reconnect attempt {attempt}/3 failed")
            time.sleep(2 * attempt)  # backoff: 2s, 4s, 6s
        log.error("MT5 reconnect failed after 3 attempts")
        raise ConnectionError("MT5 connection lost and could not be restored")

    # =====================================================================
    #  ACCOUNT
    # =====================================================================

    def account_info(self) -> dict:
        """Return account info as a dict."""
        self.ensure_connected()
        info = mt5.account_info()
        if info is None:
            return {}
        return info._asdict()

    def account_equity(self) -> float:
        self.ensure_connected()
        info = mt5.account_info()
        return info.equity if info else 0.0

    def account_balance(self) -> float:
        self.ensure_connected()
        info = mt5.account_info()
        return info.balance if info else 0.0

    # =====================================================================
    #  SYMBOLS
    # =====================================================================

    def get_all_symbols(self) -> list[dict]:
        """Return all visible, tradeable symbols as list of dicts."""
        self.ensure_connected()
        symbols = mt5.symbols_get()
        if symbols is None:
            log.warning("symbols_get returned None")
            return []
        result = []
        for s in symbols:
            if s.visible and s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL:
                result.append(s._asdict())
        log.info(f"Discovered {len(result)} tradeable symbols")
        return result

    def get_symbols_by_groups(self, groups: list[str] | None = None) -> list[str]:
        """Get symbol *names* matching group filters defined in config.
        Auto-selects symbols into MarketWatch if needed."""
        self.ensure_connected()
        groups = groups or cfg.SCAN_GROUPS
        seen: set[str] = set()
        names: list[str] = []
        for grp in groups:
            syms = mt5.symbols_get(group=grp)
            if syms:
                for s in syms:
                    if (
                        s.name not in seen
                        and s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL
                        and s.name not in cfg.EXCLUDE_SYMBOLS
                    ):
                        # Auto-select into MarketWatch if not visible
                        if not s.visible:
                            mt5.symbol_select(s.name, True)
                        seen.add(s.name)
                        names.append(s.name)
        log.info(f"Filtered universe: {len(names)} symbols from {len(groups)} groups")
        return names

    def select_symbol(self, symbol: str) -> bool:
        """Ensure *symbol* is visible in MarketWatch."""
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
        """Return full symbol info as dict, or None."""
        self.ensure_connected()
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return info._asdict()

    def symbol_tick(self, symbol: str) -> Optional[dict]:
        """Return latest tick as dict."""
        self.ensure_connected()
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return tick._asdict()

    def spread_pips(self, symbol: str) -> float:
        """
        Current spread in 'pips' — normalised for the instrument type.

        For standard forex (digits 3 or 5) 1 pip = 10 points.
        For other instruments (gold, indices, crypto) we report the spread
        as a multiple of ATR-based 'typical spread' or simply in points,
        keeping it comparable to the MAX_SPREAD_PIPS threshold.

        To avoid false rejections on non-forex (where "pips" are meaningless),
        we normalise: spread_pips = spread_in_points / pip_factor.
        """
        self.ensure_connected()
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if tick is None or info is None:
            return 999.0
        raw_spread = tick.ask - tick.bid
        point = info.point
        if point == 0:
            return 999.0
        # Zero or negative spread = frozen symbol / no LP quoting
        if raw_spread <= 0:
            return 999.0

        digits = info.digits
        # Standard forex pairs: 3/5-digit quoting → 1 pip = 10 points
        if digits in (3, 5):
            return raw_spread / (point * 10)

        # For everything else (gold 2-digit, indices 0-2 digit, crypto)
        # Use the broker's built-in spread in points (integer), then
        # normalise to a "pip-equivalent" using trade_tick_size.
        # This makes MAX_SPREAD_PIPS work as "max spread in ticks".
        tick_size = info.trade_tick_size if hasattr(info, "trade_tick_size") else point
        if tick_size > 0:
            return raw_spread / tick_size
        return raw_spread / point

    # =====================================================================
    #  HISTORICAL DATA
    # =====================================================================

    def get_rates(
        self,
        symbol: str,
        timeframe: str,
        count: int | None = None,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV bars and return as a DataFrame.

        Parameters
        ----------
        symbol : str
            Instrument name.
        timeframe : str
            Key from TF_MAP, e.g. "H1", "M15".
        count : int, optional
            Number of bars.  Defaults to ``BARS_PER_TIMEFRAME[timeframe]``.
        """
        self.ensure_connected()
        tf_mt5 = TF_MAP.get(timeframe)
        if tf_mt5 is None:
            log.error(f"Unknown timeframe: {timeframe}")
            return None

        if count is None:
            count = cfg.BARS_PER_TIMEFRAME.get(timeframe, 500)

        if not self.select_symbol(symbol):
            return None

        rates = mt5.copy_rates_from_pos(symbol, tf_mt5, 0, count)
        if rates is None or len(rates) == 0:
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("time", inplace=True)
        df.rename(
            columns={
                "tick_volume": "volume",
                "real_volume": "real_volume",
            },
            inplace=True,
        )
        return df

    def get_ticks(
        self,
        symbol: str,
        count: int = 1000,
    ) -> Optional[pd.DataFrame]:
        """Fetch recent ticks."""
        self.ensure_connected()
        if not self.select_symbol(symbol):
            return None
        # Fetch ticks from 5 minutes ago to ensure we get recent data
        utc_from = datetime.now(timezone.utc) - timedelta(minutes=5)
        ticks = mt5.copy_ticks_from(symbol, utc_from, count, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return None
        df = pd.DataFrame(ticks)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    # =====================================================================
    #  ORDERS & POSITIONS (read)
    # =====================================================================

    def open_positions(self, symbol: str | None = None) -> list[dict]:
        """Return open positions, optionally filtered by symbol."""
        self.ensure_connected()
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()
        if positions is None:
            return []
        return [p._asdict() for p in positions]

    def our_positions(self) -> list[dict]:
        """Return only positions opened by this system (matching MAGIC)."""
        return [
            p for p in self.open_positions()
            if p.get("magic") == cfg.MAGIC_NUMBER
        ]

    def pending_orders(self, symbol: str | None = None) -> list[dict]:
        self.ensure_connected()
        if symbol:
            orders = mt5.orders_get(symbol=symbol)
        else:
            orders = mt5.orders_get()
        if orders is None:
            return []
        return [o._asdict() for o in orders]

    # =====================================================================
    #  ORDER OPERATIONS
    # =====================================================================

    def calc_margin(self, action: int, symbol: str, volume: float, price: float) -> Optional[float]:
        """Calculate margin required for a trade."""
        self.ensure_connected()
        return mt5.order_calc_margin(action, symbol, volume, price)

    def calc_profit(
        self, action: int, symbol: str, volume: float,
        price_open: float, price_close: float,
    ) -> Optional[float]:
        """Calculate expected profit."""
        self.ensure_connected()
        return mt5.order_calc_profit(action, symbol, volume, price_open, price_close)

    def check_order(self, request: dict) -> Optional[dict]:
        """Validate a trade request without sending it."""
        self.ensure_connected()
        result = mt5.order_check(request)
        if result is None:
            log.error(f"order_check returned None: {mt5.last_error()}")
            return None
        return result._asdict()

    def send_order(self, request: dict) -> Optional[dict]:
        """Send a trade request to the server."""
        self.ensure_connected()
        result = mt5.order_send(request)
        if result is None:
            log.error(f"order_send returned None: {mt5.last_error()}")
            return None
        rd = result._asdict()
        if rd.get("retcode") not in (mt5.TRADE_RETCODE_DONE, 10010):
            log.warning(
                f"order_send non-success: retcode={rd.get('retcode')} "
                f"comment={rd.get('comment')} symbol={request.get('symbol')}"
            )
        return rd

    # =====================================================================
    #  HISTORY
    # =====================================================================

    def history_deals(
        self, from_date: datetime, to_date: datetime | None = None
    ) -> list[dict]:
        """Fetch historical deals."""
        self.ensure_connected()
        to_date = to_date or datetime.now(timezone.utc)
        deals = mt5.history_deals_get(from_date, to_date)
        if deals is None:
            return []
        return [d._asdict() for d in deals]
