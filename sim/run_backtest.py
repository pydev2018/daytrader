from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import time

from sim.sim_config import BacktestConfig
from sim.data import load_mt5_history
from sim.engine import BacktestEngine


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Realistic multi-symbol phased-grid backtest")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. EURCAD,EURUSD")
    parser.add_argument("--start", required=True, help="UTC start datetime, e.g. 2025-01-01T00:00:00")
    parser.add_argument("--end", required=True, help="UTC end datetime, e.g. 2025-12-31T23:59:59")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--leverage", type=float, default=30.0)
    parser.add_argument("--lot", type=float, default=0.01)
    parser.add_argument("--commission", type=float, default=0.0, help="Per-lot per-side commission in account currency")
    parser.add_argument("--swap-long", type=float, default=0.0, help="Long swap per lot per day")
    parser.add_argument("--swap-short", type=float, default=0.0, help="Short swap per lot per day")
    parser.add_argument("--slip-base", type=float, default=0.1, help="Base slippage pips")
    parser.add_argument("--slip-range", type=float, default=0.05, help="Range-based slippage multiplier")
    parser.add_argument("--slip-trend", type=float, default=0.10, help="Trend slippage multiplier")
    parser.add_argument("--paired-ledger", default="true", help="true/false paired ledger gating for counter-trend entries")
    parser.add_argument("--paired-risk-steps", type=float, default=1.0, help="Risk reserve steps per counter-trend entry")
    parser.add_argument("--progress-every", type=int, default=2000, help="Print progress every N timeline steps")
    parser.add_argument("--output", default="logs/backtest_report.json", help="Output JSON report path")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    start = _parse_dt(args.start)
    end = _parse_dt(args.end)

    market_data, specs = load_mt5_history(symbols, start, end, args.timeframe)
    if not market_data:
        raise SystemExit(
            "No market data loaded from MT5. Check symbol names/suffixes, "
            "chart history availability in terminal, and date range."
        )

    print(f"[BACKTEST] Loaded symbols: {', '.join(market_data.keys())}")
    for sym, df in market_data.items():
        print(f"[BACKTEST] {sym}: {len(df)} bars ({df.iloc[0]['time']} -> {df.iloc[-1]['time']})")

    cfg = BacktestConfig(
        start=start,
        end=end,
        timeframe=args.timeframe,
        initial_balance=args.balance,
        leverage=args.leverage,
        lot_size=args.lot,
        commission_per_lot_per_side=args.commission,
        long_swap_per_lot_per_day=args.swap_long,
        short_swap_per_lot_per_day=args.swap_short,
        base_slippage_pips=args.slip_base,
        range_slippage_mult=args.slip_range,
        trend_slippage_mult=args.slip_trend,
        paired_ledger_enabled=str(args.paired_ledger).lower() in ("true", "1", "yes"),
        paired_risk_steps=args.paired_risk_steps,
        progress_every_steps=args.progress_every,
    )

    engine = BacktestEngine(config=cfg, market_data=market_data, specs=specs)
    t0 = time.perf_counter()
    report = engine.run()
    elapsed = time.perf_counter() - t0

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 70)
    print("PHASED GRID BACKTEST COMPLETE")
    print("=" * 70)
    print(f"Symbols: {', '.join(market_data.keys())}")
    print(f"Initial balance: {report['initial_balance']:.2f}")
    print(f"Final equity:    {report['final_equity']:.2f}")
    print(f"Return %:        {report['total_return_pct']:.2f}%")
    print(f"Max drawdown %:  {report['max_drawdown_pct']:.2f}%")
    print(f"Events:          {len(report['events'])}")
    print(f"Elapsed sec:     {elapsed:.1f}")
    print(f"Report:          {out_path}")


if __name__ == "__main__":
    main()
