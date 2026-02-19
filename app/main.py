"""OCO breakout system — entry point."""

from __future__ import annotations

import argparse

from brokers.mt5 import MT5Broker
from config import settings as cfg
from oco.engine import OcoEngine
from utils.logger import setup_logging


def show_status():
    broker = MT5Broker()
    if not broker.connect():
        print("ERROR: Cannot connect to MT5")
        return
    acc = broker.account_info()
    print("\n" + "=" * 60)
    print("  OCO BREAKOUT SYSTEM -- Account Status")
    print("=" * 60)
    print(f"  Account:    {acc.get('login')}")
    print(f"  Server:     {acc.get('server')}")
    print(f"  Balance:    ${acc.get('balance', 0):,.2f}")
    print(f"  Equity:     ${acc.get('equity', 0):,.2f}")
    print(f"  Margin:     ${acc.get('margin', 0):,.2f}")
    print(f"  Free Margin:${acc.get('margin_free', 0):,.2f}")
    print(f"  Leverage:   1:{acc.get('leverage', 0)}")
    positions = broker.our_positions()
    print(f"\n  Open positions (magic={cfg.MAGIC_NUMBER}): {len(positions)}")
    for pos in positions:
        sym = pos.get("symbol", "?")
        direction = "BUY" if pos.get("type", 0) == 0 else "SELL"
        pnl = pos.get("profit", 0)
        vol = pos.get("volume", 0)
        print(f"    {sym:12s} {direction:4s} {vol:.2f} lots  PnL=${pnl:+.2f}")
    pending = broker.pending_orders()
    oco_pending = [o for o in pending if (o.get("comment", "").lower().startswith("oco:"))]
    print(f"  Pending OCO orders: {len(oco_pending)}")
    print("=" * 60 + "\n")
    broker.disconnect()


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="OCO Breakout System")
    parser.add_argument("--status", action="store_true",
                        help="Show account status and exit")
    args = parser.parse_args()

    if args.status:
        show_status()
        return
    engine = OcoEngine()
    engine.start()


if __name__ == "__main__":
    main()
