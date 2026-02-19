"""
MT5 + OANDA smoke test (safe, no orders placed).

Checks:
- MT5 connection and account model (hedging/netting/FIFO)
- Symbol info/tick retrieval
- order_check acceptance for different filling modes (pending + market)
"""

from __future__ import annotations

import MetaTrader5 as mt5

from brokers.mt5 import MT5Broker
from config import settings as cfg


def _print_check(label: str, result: dict | None):
    if result is None:
        print(f"{label}: None (order_check failed)")
        return
    retcode = result.get("retcode")
    comment = result.get("comment", "")
    margin = result.get("margin", 0.0)
    print(f"{label}: retcode={retcode} comment={comment} margin={margin}")


def main():
    broker = MT5Broker()
    if not broker.connect():
        print("ERROR: MT5 connect failed")
        return

    acc = broker.account_info()
    model = broker.account_model()
    print(
        f"Account: {acc.get('login')} server={acc.get('server')} "
        f"balance={acc.get('balance')} equity={acc.get('equity')}"
    )
    print(
        f"Model: hedging={model.get('is_hedging')} "
        f"netting={model.get('is_netting')} fifo={model.get('fifo_close')}"
    )

    symbol = cfg.OCO_SYMBOLS[0] if cfg.OCO_SYMBOLS else "EURUSD"
    if not broker.select_symbol(symbol):
        print(f"ERROR: Cannot select symbol {symbol}")
        broker.disconnect()
        return

    info = broker.symbol_info(symbol) or {}
    tick = broker.symbol_tick(symbol)
    if not tick:
        print(f"ERROR: No tick for {symbol}")
        broker.disconnect()
        return

    print(
        f"Symbol: {symbol} digits={info.get('digits')} "
        f"point={info.get('point')} tick_size={info.get('trade_tick_size')} "
        f"vol_min={info.get('volume_min')} vol_step={info.get('volume_step')} "
        f"contract={info.get('trade_contract_size')} "
        f"filling_mode={info.get('filling_mode')} "
        f"trade_fill_flags={info.get('trade_fill_flags')}"
    )

    tick_size = info.get("trade_tick_size") or info.get("point", 0.0)
    min_dist = broker.get_min_distance(symbol)
    distance = max(min_dist * 2, tick_size * 20)

    buy_limit = broker.normalize_price(symbol, tick["bid"] - distance)
    volume = info.get("volume_min", 0.01)

    modes = [
        (mt5.ORDER_FILLING_IOC, "IOC"),
        (mt5.ORDER_FILLING_RETURN, "RETURN"),
        (mt5.ORDER_FILLING_FOK, "FOK"),
    ]

    print("\norder_check — pending BUY_LIMIT")
    for mode, name in modes:
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY_LIMIT,
            "price": buy_limit,
            "magic": cfg.MAGIC_NUMBER,
            "comment": "smoke_test_pending",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mode,
        }
        result = broker.check_order(request)
        _print_check(f"  {name}", result)

    print("\norder_check — market BUY")
    for mode, name in modes:
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY,
            "price": tick["ask"],
            "deviation": 20,
            "magic": cfg.MAGIC_NUMBER,
            "comment": "smoke_test_market",
            "type_filling": mode,
        }
        result = broker.check_order(request)
        _print_check(f"  {name}", result)

    broker.disconnect()
    print("\nSmoke test complete (no orders placed).")


if __name__ == "__main__":
    main()
