from __future__ import annotations

import math
from typing import Optional

import MetaTrader5 as mt5
import pandas as pd

from config import settings as cfg
from utils.logger import get_logger

log = get_logger("mt5")

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


class MT5Broker:
    def __init__(self) -> None:
        self._connected = False

    def connect(self) -> bool:
        kwargs: dict = {"timeout": cfg.MT5_TIMEOUT_MS}
        if cfg.MT5_PATH:
            kwargs["path"] = cfg.MT5_PATH
        if cfg.MT5_LOGIN:
            kwargs["login"] = cfg.MT5_LOGIN
        if cfg.MT5_PASSWORD:
            kwargs["password"] = cfg.MT5_PASSWORD
        if cfg.MT5_SERVER:
            kwargs["server"] = cfg.MT5_SERVER

        if not mt5.initialize(**kwargs):
            log.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False
        self._connected = True
        return True

    def disconnect(self) -> None:
        mt5.shutdown()
        self._connected = False

    def ensure_connected(self) -> None:
        if self._connected and mt5.terminal_info() is not None:
            return
        if not self.connect():
            raise ConnectionError("MT5 connection unavailable")

    def account_info(self) -> dict:
        self.ensure_connected()
        info = mt5.account_info()
        return info._asdict() if info else {}

    def account_model(self) -> dict:
        acc = self.account_info()
        mode = acc.get("margin_mode")
        is_hedging = mode == getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", -1)
        return {
            "is_hedging": is_hedging,
            "is_netting": not is_hedging,
            "fifo_close": bool(acc.get("fifo_close", False)),
        }

    def select_symbol(self, symbol: str) -> bool:
        self.ensure_connected()
        info = mt5.symbol_info(symbol)
        if info is None:
            return False
        if not info.visible:
            return bool(mt5.symbol_select(symbol, True))
        return True

    def symbol_info(self, symbol: str) -> Optional[dict]:
        self.ensure_connected()
        info = mt5.symbol_info(symbol)
        return info._asdict() if info else None

    def symbol_tick(self, symbol: str) -> Optional[dict]:
        self.ensure_connected()
        tick = mt5.symbol_info_tick(symbol)
        return tick._asdict() if tick else None

    def get_rates(self, symbol: str, timeframe: str, count: int) -> Optional[pd.DataFrame]:
        self.ensure_connected()
        tf = TF_MAP.get(timeframe, mt5.TIMEFRAME_M5)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def our_positions(self, symbol: str | None = None) -> list[dict]:
        self.ensure_connected()
        data = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if data is None:
            return []
        return [p._asdict() for p in data if p.magic == cfg.MAGIC_NUMBER]

    def our_pending_orders(self, symbol: str | None = None) -> list[dict]:
        self.ensure_connected()
        data = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
        if data is None:
            return []
        return [o._asdict() for o in data if o.magic == cfg.MAGIC_NUMBER]

    def send_order(self, request: dict) -> Optional[dict]:
        self.ensure_connected()
        result = mt5.order_send(request)
        if result is None:
            log.error(f"order_send failed: {mt5.last_error()}")
            return None
        return result._asdict()

    def cancel_order(self, ticket: int) -> Optional[dict]:
        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(ticket),
            "magic": cfg.MAGIC_NUMBER,
            "comment": "phased_grid_cancel",
        }
        return self.send_order(req)

    @staticmethod
    def normalize_price(symbol_info: dict, price: float) -> float:
        digits = int(symbol_info.get("digits", 5))
        return round(float(price), digits)

    @staticmethod
    def normalize_volume(symbol_info: dict, volume: float) -> float:
        step = float(symbol_info.get("volume_step", 0.01))
        min_vol = float(symbol_info.get("volume_min", step))
        max_vol = float(symbol_info.get("volume_max", 100.0))
        aligned = math.floor(float(volume) / step) * step
        aligned = max(min_vol, min(max_vol, aligned))
        decimals = max(0, len(str(step).split(".")[-1]) if "." in str(step) else 0)
        return round(aligned, decimals)

    @staticmethod
    def pip_size(symbol_info: dict) -> float:
        digits = int(symbol_info.get("digits", 5))
        point = float(symbol_info.get("point", 0.00001))
        if digits in (3, 5):
            return 10.0 * point
        return point
