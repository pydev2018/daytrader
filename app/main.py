"""
Grid Trading System — Entry Point.
"""

from __future__ import annotations

import argparse

from brokers.mt5 import MT5Broker
from grid.engine import GridEngine
from utils.logger import setup_logging


def show_status():
    broker = MT5Broker()
    if not broker.connect():
        print("ERROR: Cannot connect to MT5")
        return
    acc = broker.account_info()
    print("\n" + "=" * 60)
    print("  GRID TRADING SYSTEM -- Account Status")
    print("=" * 60)
    print(f"  Account:    {acc.get('login')}")
    print(f"  Server:     {acc.get('server')}")
    print(f"  Balance:    ${acc.get('balance', 0):,.2f}")
    print(f"  Equity:     ${acc.get('equity', 0):,.2f}")
    print(f"  Margin:     ${acc.get('margin', 0):,.2f}")
    print(f"  Free Margin:${acc.get('margin_free', 0):,.2f}")
    print(f"  Leverage:   1:{acc.get('leverage', 0)}")
    positions = broker.our_positions()
    print(f"\n  Open positions (GRID): {len(positions)}")
    for pos in positions:
        sym = pos.get("symbol", "?")
        direction = "BUY" if pos.get("type", 0) == 0 else "SELL"
        pnl = pos.get("profit", 0)
        vol = pos.get("volume", 0)
        print(f"    {sym:12s} {direction:4s} {vol:.2f} lots  PnL=${pnl:+.2f}")
    print("=" * 60 + "\n")
    broker.disconnect()


def run_scan(symbols: list[str] | None = None, top_n: int = 10):
    """Scan symbols and display grid-suitability rankings."""
    from grid.scanner import GridScanner, FX_UNIVERSE

    broker = MT5Broker()
    if not broker.connect():
        print("ERROR: Cannot connect to MT5")
        return

    universe = symbols or FX_UNIVERSE
    print(f"\nScanning {len(universe)} symbols for grid suitability ...\n")

    scanner = GridScanner(broker)
    results = scanner.scan(universe, timeframe="M5", bar_count=500)

    if not results:
        print("No symbols could be scanned.")
        broker.disconnect()
        return

    # Header
    print("=" * 95)
    print(f"  {'#':>2}  {'Symbol':<10} {'Score':>6} {'Verdict':<10} "
          f"{'MeanRev':>8} {'CostEff':>8} {'VolReg':>7} {'SprdStb':>8} "
          f"{'NoTrend':>8}")
    print("-" * 95)

    for i, r in enumerate(results[:top_n], 1):
        # Color-code verdict
        v = r.verdict
        print(f"  {i:>2}  {r.symbol:<10} {r.score:>6.1f} {v:<10} "
              f"{r.mean_reversion:>8.3f} {r.cost_efficiency:>8.3f} "
              f"{r.vol_regime:>7.3f} {r.spread_stability:>8.3f} "
              f"{r.trend_absence:>8.3f}")

    # Detailed breakdown of top 3
    print("\n" + "=" * 95)
    print("  TOP PICKS — Detailed Analysis")
    print("=" * 95)

    for r in results[:min(3, len(results))]:
        d = r.details
        print(f"\n  {r.symbol} — Score: {r.score}/100 ({r.verdict})")
        print(f"    Hurst exponent:  {d.get('hurst', 0):.4f}"
              f"  ({'mean-reverting' if d.get('hurst', 0.5) < 0.45 else 'trending' if d.get('hurst', 0.5) > 0.55 else 'random walk'})")
        print(f"    Variance ratio:  {d.get('variance_ratio', 1):.4f}"
              f"  ({'mean-reverting' if d.get('variance_ratio', 1) < 0.9 else 'trending' if d.get('variance_ratio', 1) > 1.1 else 'neutral'})")
        print(f"    Spread:          {d.get('spread_pips', 0):.1f} pips")
        print(f"    ATR:             {d.get('atr_pips', 0):.1f} pips")
        print(f"    Cost/ATR ratio:  {d.get('cost_ratio_pct', 0):.2f}%")
        print(f"    Ann. volatility: {d.get('ann_vol_pct', 0):.2f}%")
        print(f"    Trend z-score:   {d.get('trend_z', 0):.2f}")
        print(f"    Spread CV:       {d.get('spread_cv', 0):.3f}")

    # Bottom 3 (avoid)
    if len(results) > 3:
        print("\n" + "-" * 95)
        print("  WORST CANDIDATES (avoid for grid)")
        print("-" * 95)
        for r in results[-min(3, len(results)):]:
            d = r.details
            reasons = []
            if d.get("hurst", 0.5) > 0.55:
                reasons.append("trending")
            if d.get("cost_ratio_pct", 0) > 20:
                reasons.append("expensive")
            if abs(d.get("trend_z", 0)) > 2:
                reasons.append("strong trend")
            if d.get("ann_vol_pct", 0) > 25:
                reasons.append("too volatile")
            print(f"  {r.symbol:<10} Score: {r.score:>5.1f}  "
                  f"Reasons: {', '.join(reasons) if reasons else 'low composite'}")

    print("\n" + "=" * 95)
    print(f"  Recommendation: Trade the top {min(3, len(results))} symbols")
    top_syms = [r.symbol for r in results[:min(3, len(results))] if r.score >= 30]
    if top_syms:
        print(f"  Set GRID_SYMBOLS={','.join(top_syms)} in .env")
    print("=" * 95 + "\n")

    broker.disconnect()


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Grid Trading System")
    parser.add_argument("--status", action="store_true",
                        help="Show account status and exit")
    parser.add_argument("--scan", action="store_true",
                        help="Scan symbols for grid suitability")
    parser.add_argument("--scan-symbols", type=str, default="",
                        help="Comma-separated symbols to scan (default: full FX universe)")
    parser.add_argument("--top", type=int, default=10,
                        help="Number of top results to show (default: 10)")
    args = parser.parse_args()

    if args.status:
        show_status()
        return
    if args.scan:
        symbols = None
        if args.scan_symbols:
            symbols = [s.strip() for s in args.scan_symbols.split(",") if s.strip()]
        run_scan(symbols, top_n=args.top)
        return

    engine = GridEngine()
    engine.start()


if __name__ == "__main__":
    main()
