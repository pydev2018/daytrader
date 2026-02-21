"""Cointegrated strategy entrypoint (pure branch mode)."""

from __future__ import annotations

import argparse

from brokers.mt5 import MT5Broker
from config import settings as cfg
from strategies.cointegrated.engine import CointegratedEngine
from strategies.cointegrated.pair_finder import PairFinder, format_pair_result
from utils.logger import setup_logging


def show_status():
    broker = MT5Broker()
    if not broker.connect():
        print("ERROR: Cannot connect to MT5")
        return
    acc = broker.account_info()
    print("\n" + "=" * 60)
    print("  COINTEGRATED STRATEGY -- Account Status")
    print("=" * 60)
    print(f"  Account:    {acc.get('login')}")
    print(f"  Server:     {acc.get('server')}")
    print(f"  Balance:    ${acc.get('balance', 0):,.2f}")
    print(f"  Equity:     ${acc.get('equity', 0):,.2f}")
    print(f"  Margin:     ${acc.get('margin', 0):,.2f}")
    print(f"  Free Margin:${acc.get('margin_free', 0):,.2f}")
    print(f"  Leverage:   1:{acc.get('leverage', 0)}")
    positions = broker.our_positions()
    print(f"\n  Open positions (strategy magic): {len(positions)}")
    for pos in positions:
        sym = pos.get("symbol", "?")
        direction = "BUY" if pos.get("type", 0) == 0 else "SELL"
        pnl = pos.get("profit", 0)
        vol = pos.get("volume", 0)
        print(f"    {sym:12s} {direction:4s} {vol:.2f} lots  PnL=${pnl:+.2f}")
    print("=" * 60 + "\n")
    broker.disconnect()


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Cointegrated Strategy System")
    parser.add_argument("--status", action="store_true", help="Show account status and exit")
    parser.add_argument("--pair-check", action="store_true", help="Print pair-check workflow status")
    parser.add_argument("--pair-x", type=str, default="", help="First symbol for pair check")
    parser.add_argument("--pair-y", type=str, default="", help="Second symbol for pair check")
    parser.add_argument("--pair-timeframe", type=str, default="", help="Timeframe for pair check (e.g. M5)")
    parser.add_argument("--pair-bars", type=int, default=0, help="Bar count for pair check")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.pair_check:
        ok, msg = PairFinder.dependency_ok()
        if not ok:
            print(f"ERROR: {msg}")
            return

        pair_x = (args.pair_x or (cfg.COINT_SYMBOLS[0] if len(cfg.COINT_SYMBOLS) >= 1 else "")).strip()
        pair_y = (args.pair_y or (cfg.COINT_SYMBOLS[1] if len(cfg.COINT_SYMBOLS) >= 2 else "")).strip()
        timeframe = (args.pair_timeframe or cfg.COINT_TIMEFRAME).strip().upper()
        bars = int(args.pair_bars or cfg.COINT_BAR_COUNT)

        if not pair_x or not pair_y:
            print("ERROR: pair symbols are required. Set COINT_SYMBOLS or pass --pair-x and --pair-y.")
            return

        broker = MT5Broker()
        if not broker.connect():
            print("ERROR: Cannot connect to MT5")
            return
        try:
            finder = PairFinder(broker)
            result = finder.run_and_persist(pair_x, pair_y, timeframe, bars)
            print(format_pair_result(result))
        finally:
            broker.disconnect()
        return

    engine = CointegratedEngine()
    engine.start(pair_check_only=False)


if __name__ == "__main__":
    main()
