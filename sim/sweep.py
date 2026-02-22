from __future__ import annotations

import argparse
import csv
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

from sim.config import BacktestConfig
from sim.data import load_mt5_history
from sim.engine import BacktestEngine


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_list(value: str, cast):
    return [cast(item.strip()) for item in value.split(",") if item.strip()]


def _objective(score_mode: str, total_return_pct: float, max_drawdown_pct: float) -> float:
    if score_mode == "return":
        return total_return_pct
    if score_mode == "return_dd":
        return total_return_pct - 0.5 * max_drawdown_pct
    return total_return_pct


def main() -> None:
    parser = argparse.ArgumentParser(description="Parameter sweep for phased-grid backtest")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--leverage", type=float, default=30.0)
    parser.add_argument("--lot", type=float, default=0.01)
    parser.add_argument("--commission", type=float, default=0.0)
    parser.add_argument("--swap-long", type=float, default=0.0)
    parser.add_argument("--swap-short", type=float, default=0.0)
    parser.add_argument("--slip-base", type=float, default=0.1)
    parser.add_argument("--slip-range", type=float, default=0.05)
    parser.add_argument("--slip-trend", type=float, default=0.10)

    parser.add_argument("--step-atr-mults", default="0.9,1.1")
    parser.add_argument("--step-spread-mults", default="10,12,14")
    parser.add_argument("--offset-ratios", default="0.5")
    parser.add_argument("--trend-on-adx-list", default="24,25,27")
    parser.add_argument("--trend-off-adx-list", default="18,20,22")
    parser.add_argument("--slow-trend-bars-list", default="4,5,6")
    parser.add_argument("--slow-trend-move-steps-list", default="0.8,1.0,1.2")
    parser.add_argument("--cleanup-count-list", default="1,2")
    parser.add_argument("--risk-unwind-list", default="1,2")
    parser.add_argument("--paired-ledger", default="true", help="true/false paired ledger gating for counter-trend entries")
    parser.add_argument("--paired-risk-steps", type=float, default=1.0, help="Risk reserve steps per new counter-trend entry")

    parser.add_argument("--score", choices=["return", "return_dd"], default="return_dd")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--progress-every", type=int, default=3000)
    parser.add_argument("--output-json", default="logs/sweep_results.json")
    parser.add_argument("--output-csv", default="logs/sweep_results.csv")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    start = _parse_dt(args.start)
    end = _parse_dt(args.end)

    market_data, specs = load_mt5_history(symbols, start, end, args.timeframe)
    if not market_data:
        raise SystemExit("No market data loaded for sweep")

    step_atr_mults = _parse_list(args.step_atr_mults, float)
    step_spread_mults = _parse_list(args.step_spread_mults, float)
    offset_ratios = _parse_list(args.offset_ratios, float)
    trend_on_list = _parse_list(args.trend_on_adx_list, float)
    trend_off_list = _parse_list(args.trend_off_adx_list, float)
    slow_bars_list = _parse_list(args.slow_trend_bars_list, int)
    slow_move_steps_list = _parse_list(args.slow_trend_move_steps_list, float)
    cleanup_list = _parse_list(args.cleanup_count_list, int)
    unwind_list = _parse_list(args.risk_unwind_list, int)

    combos = list(
        itertools.product(
            step_atr_mults,
            step_spread_mults,
            offset_ratios,
            trend_on_list,
            trend_off_list,
            slow_bars_list,
            slow_move_steps_list,
            cleanup_list,
            unwind_list,
        )
    )

    print(f"[SWEEP] symbols={','.join(market_data.keys())} combos={len(combos)}")

    rows: list[dict] = []
    for idx, combo in enumerate(combos, start=1):
        (
            step_atr_mult,
            step_spread_mult,
            offset_ratio,
            trend_on,
            trend_off,
            slow_bars,
            slow_move_steps,
            cleanup_count,
            unwind_count,
        ) = combo

        if trend_on <= trend_off:
            continue

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
            step_atr_mult=step_atr_mult,
            step_spread_mult=step_spread_mult,
            offset_ratio=offset_ratio,
            trend_on_adx=trend_on,
            trend_off_adx=trend_off,
            slow_trend_bars=slow_bars,
            slow_trend_min_move_steps=slow_move_steps,
            cleanup_close_count=cleanup_count,
            risk_off_unwind_per_cycle=unwind_count,
            protect_oscillation_bank=True,
            paired_ledger_enabled=str(args.paired_ledger).lower() in ("true", "1", "yes"),
            paired_risk_steps=args.paired_risk_steps,
            progress_every_steps=args.progress_every,
        )

        engine = BacktestEngine(config=cfg, market_data=market_data, specs=specs)
        report = engine.run()

        total_return_pct = float(report.get("total_return_pct", 0.0))
        max_drawdown_pct = float(report.get("max_drawdown_pct", 0.0))
        event_count = len(report.get("events", []))

        row = {
            "run": idx,
            "symbols": ",".join(market_data.keys()),
            "return_pct": total_return_pct,
            "max_dd_pct": max_drawdown_pct,
            "final_equity": float(report.get("final_equity", 0.0)),
            "events": event_count,
            "score": _objective(args.score, total_return_pct, max_drawdown_pct),
            "step_atr_mult": step_atr_mult,
            "step_spread_mult": step_spread_mult,
            "offset_ratio": offset_ratio,
            "trend_on_adx": trend_on,
            "trend_off_adx": trend_off,
            "slow_trend_bars": slow_bars,
            "slow_trend_move_steps": slow_move_steps,
            "cleanup_count": cleanup_count,
            "risk_unwind_count": unwind_count,
        }
        rows.append(row)

        if idx % 10 == 0:
            print(f"[SWEEP] completed {idx}/{len(combos)}")

    rows.sort(key=lambda r: r["score"], reverse=True)

    out_json = Path(args.output_json)
    out_csv = Path(args.output_csv)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    out_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    if rows:
        fieldnames = list(rows[0].keys())
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    top_n = min(args.top, len(rows))
    print("=" * 70)
    print(f"SWEEP COMPLETE: {len(rows)} runs")
    print(f"Top {top_n} by {args.score}")
    print("=" * 70)
    for i in range(top_n):
        r = rows[i]
        print(
            f"#{i+1} score={r['score']:.3f} return={r['return_pct']:.3f}% dd={r['max_dd_pct']:.3f}% "
            f"atr={r['step_atr_mult']} spread={r['step_spread_mult']} on/off={r['trend_on_adx']}/{r['trend_off_adx']} "
            f"slow={r['slow_trend_bars']}@{r['slow_trend_move_steps']} cleanup={r['cleanup_count']} unwind={r['risk_unwind_count']}"
        )
    print(f"JSON: {out_json}")
    print(f"CSV:  {out_csv}")


if __name__ == "__main__":
    main()
