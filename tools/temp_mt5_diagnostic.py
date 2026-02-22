from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
from dotenv import load_dotenv


TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def init_mt5() -> bool:
    base_dir = Path(__file__).resolve().parents[1]
    load_dotenv(base_dir / ".env")

    kwargs: dict = {"timeout": int(os.getenv("MT5_TIMEOUT_MS", "60000"))}
    path = os.getenv("MT5_PATH", "")
    login_raw = (os.getenv("MT5_LOGIN", "0") or "0").strip()
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    if path:
        kwargs["path"] = path
    if login_raw and login_raw != "0":
        kwargs["login"] = int(login_raw)
    if password:
        kwargs["password"] = password
    if server:
        kwargs["server"] = server

    ok = mt5.initialize(**kwargs)
    print(f"initialize: {ok}")
    print(f"last_error: {mt5.last_error()}")
    return ok


def resolve_symbol(requested: str, all_symbols: list[str]) -> tuple[str | None, list[str]]:
    if requested in all_symbols:
        return requested, []

    upper = requested.upper()
    candidates = [s for s in all_symbols if s.upper() == upper]
    if candidates:
        return candidates[0], candidates[1:]

    # Common broker variants: EURUSD.a, EURUSDm, xEURUSD, etc.
    fuzzy = [
        s
        for s in all_symbols
        if s.upper().startswith(upper)
        or s.upper().endswith(upper)
        or upper in s.upper()
    ]
    if fuzzy:
        return fuzzy[0], fuzzy[1:]

    return None, []


def main() -> None:
    parser = argparse.ArgumentParser(description="Temporary MT5 data diagnostics")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    parser.add_argument("--start", required=True, help="UTC start datetime")
    parser.add_argument("--end", required=True, help="UTC end datetime")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--show-candidates", type=int, default=8)
    args = parser.parse_args()

    start = parse_dt(args.start)
    end = parse_dt(args.end)
    tf = TF_MAP.get(args.timeframe.upper(), mt5.TIMEFRAME_M5)
    requested_symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if not init_mt5():
        return

    acc = mt5.account_info()
    term = mt5.terminal_info()
    print("-" * 70)
    print(f"terminal: {term.name if term else None}")
    print(f"account: {acc.login if acc else None}")
    print(f"server: {acc.server if acc else None}")

    all_info = mt5.symbols_get() or []
    all_symbols = [s.name for s in all_info]
    print(f"symbols_in_terminal: {len(all_symbols)}")
    print("-" * 70)

    for requested in requested_symbols:
        resolved, alternates = resolve_symbol(requested, all_symbols)
        print(f"requested: {requested}")
        print(f"resolved:  {resolved}")

        if resolved is None:
            sample = [s for s in all_symbols if requested.upper()[:3] in s.upper()][: args.show_candidates]
            print(f"candidate_sample: {sample}")
            print("status: FAILED (symbol not found)")
            print("-" * 70)
            continue

        selected = mt5.symbol_select(resolved, True)
        print(f"selected: {selected}")

        rates = mt5.copy_rates_range(resolved, tf, start, end)
        if rates is None:
            print(f"bars: None")
            print(f"last_error: {mt5.last_error()}")
            print("status: FAILED (history request returned None)")
            print("-" * 70)
            continue

        bar_count = len(rates)
        print(f"bars: {bar_count}")
        if bar_count > 0:
            first_time = datetime.fromtimestamp(int(rates[0]["time"]), tz=timezone.utc)
            last_time = datetime.fromtimestamp(int(rates[-1]["time"]), tz=timezone.utc)
            print(f"first_bar_utc: {first_time.isoformat()}")
            print(f"last_bar_utc:  {last_time.isoformat()}")
            print("status: OK")
        else:
            print("status: FAILED (zero bars in requested range)")

        if alternates:
            print(f"alternate_matches: {alternates[: args.show_candidates]}")
        print("-" * 70)

    mt5.shutdown()


if __name__ == "__main__":
    main()
