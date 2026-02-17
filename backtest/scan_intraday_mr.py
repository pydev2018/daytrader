"""
Scan all FX symbols for intraday oscillation + mean reversion quality.

Usage examples:
  python backtest/scan_intraday_mr.py
  python backtest/scan_intraday_mr.py --timeframe M5 --bars 2000 --top 10
  python backtest/scan_intraday_mr.py --symbols EURCAD,EURUSD,GBPUSD
  python backtest/scan_intraday_mr.py --csv data/mr_scan.csv
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5

from brokers.mt5 import MT5Broker
from backtest.real_data_backtest import resolve_symbol
from grid.mr_scanner import MeanReversionScanner
from grid.scanner import FX_UNIVERSE


def _parse_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _unique_keep_order(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def main():
    parser = argparse.ArgumentParser(description="Scan intraday mean-reversion symbols")
    parser.add_argument("--timeframe", default="M5", help="MT5 timeframe (default M5)")
    parser.add_argument("--bars", type=int, default=2000, help="Bars per symbol (default 2000)")
    parser.add_argument("--top", type=int, default=15, help="Show top N (0 = all)")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols to scan")
    parser.add_argument("--min-atr-spread", type=float, default=6.0,
                        help="Filter: minimum ATR/spread ratio (default 6.0)")
    parser.add_argument("--max-trend-z", type=float, default=2.5,
                        help="Filter: maximum |trend_z| (default 2.5)")
    parser.add_argument("--csv", default="", help="Optional CSV output path")
    args = parser.parse_args()

    broker = MT5Broker()
    if not broker.connect():
        print("ERROR: Cannot connect to MT5")
        return

    # Build universe
    if args.symbols:
        base_symbols = _parse_symbols(args.symbols)
    else:
        base_symbols = FX_UNIVERSE

    available = []
    base_to_real = {}

    for base in base_symbols:
        real = resolve_symbol(base)
        info = mt5.symbol_info(real)
        if info is None:
            continue
        if not info.visible:
            mt5.symbol_select(real, True)
        rates = mt5.copy_rates_from_pos(real, mt5.TIMEFRAME_M5, 0, 10)
        if rates is None or len(rates) == 0:
            continue
        available.append(real)
        base_to_real[base] = real

    available = _unique_keep_order(available)
    if not available:
        print("No symbols with data. Add them to MarketWatch and try again.")
        broker.disconnect()
        return

    print()
    print("=" * 100)
    print("  INTRADAY MEAN-REVERSION SCAN")
    print(f"  Symbols: {len(available)} | Timeframe: {args.timeframe} | Bars: {args.bars}")
    print("=" * 100)

    scanner = MeanReversionScanner(broker)
    results = scanner.scan(available, timeframe=args.timeframe, bar_count=args.bars)

    if not results:
        print("No scan results.")
        broker.disconnect()
        return

    # Filter candidates
    filtered = []
    for r in results:
        d = r.details
        atr_spread = d.get("atr_spread_ratio", 0.0)
        trend_z = abs(d.get("trend_z", 999.0))
        passes = atr_spread >= args.min_atr_spread and trend_z <= args.max_trend_z
        filtered.append((r, passes))

    if args.top > 0:
        filtered = filtered[:args.top]

    print()
    print(f"  Filter: ATR/Spread >= {args.min_atr_spread:.1f}  |  |TrendZ| <= {args.max_trend_z:.1f}")
    print("  * = passes filter")
    print()
    print(f"  {'#':>3} {'Sym':<12} {'Score':>6} {'Verdict':<9} {'MR':>5} "
          f"{'ATR/Sp':>7} {'HL':>6} {'X/hr':>6} {'TrendZ':>7} "
          f"{'Spread':>7} {'ATR':>7}")
    print("  " + "-" * 95)
    for i, (r, passes) in enumerate(filtered, 1):
        d = r.details
        mark = "*" if passes else " "
        print(f"{mark} {i:>3} {r.symbol:<12} {r.score:>6.1f} {r.verdict:<9} {r.mean_reversion:>5.2f} "
              f"{d.get('atr_spread_ratio', 0):>7.2f} {d.get('half_life_bars', 0):>6.0f} "
              f"{d.get('crossings_per_hour', 0):>6.2f} {d.get('trend_z', 0):>+7.2f} "
              f"{d.get('spread_pips', 0):>7.1f} {d.get('atr_pips', 0):>7.1f}")

    # Optional CSV output
    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "symbol", "score", "verdict", "mean_reversion", "cost_efficiency",
                "trend_absence", "oscillation", "crossings_per_hour",
                "hurst", "variance_ratio", "half_life_bars", "atr_spread_ratio",
                "trend_z", "spread_pips", "atr_pips", "osc_ratio",
            ])
            for r, _ in filtered:
                d = r.details
                writer.writerow([
                    r.symbol, r.score, r.verdict, r.mean_reversion, r.cost_efficiency,
                    r.trend_absence, r.oscillation, r.crossings,
                    d.get("hurst", ""), d.get("variance_ratio", ""), d.get("half_life_bars", ""),
                    d.get("atr_spread_ratio", ""), d.get("trend_z", ""),
                    d.get("spread_pips", ""), d.get("atr_pips", ""), d.get("osc_ratio", ""),
                ])
        print(f"\nCSV saved to: {args.csv}")

    broker.disconnect()


if __name__ == "__main__":
    main()
