"""List all symbols available in the connected MT5 terminal and save to a text file.

Usage:
  conda run -n tradebot python diagnostics/list_oanda_symbols.py
  conda run -n tradebot python diagnostics/list_oanda_symbols.py --output data/oanda_symbols.txt
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import MetaTrader5 as mt5

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings as cfg


def connect_mt5() -> bool:
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
    return mt5.initialize(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export all MT5 symbols to a text file")
    parser.add_argument(
        "--output",
        type=str,
        default=str(cfg.DATA_DIR / "oanda_symbols.txt"),
        help="Output text file path",
    )
    args = parser.parse_args()

    if not connect_mt5():
        print(f"ERROR: MT5 initialize failed: {mt5.last_error()}")
        return 1

    try:
        symbols = mt5.symbols_get()
        if symbols is None:
            print(f"ERROR: symbols_get failed: {mt5.last_error()}")
            return 1

        symbol_names = sorted({s.name for s in symbols if getattr(s, "name", "")})

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(symbol_names) + "\n", encoding="utf-8")

        print(f"Exported {len(symbol_names)} symbols to: {output_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
