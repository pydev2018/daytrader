from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Deep analyzer for phased-grid backtest report")
    parser.add_argument("--report", default="backtest_audit_report.json")
    args = parser.parse_args()

    p = Path(args.report)
    data = json.loads(p.read_text(encoding="utf-8"))

    initial_balance = float(data.get("initial_balance", 0.0))
    final_balance = float(data.get("final_balance", 0.0))
    final_equity = float(data.get("final_equity", 0.0))
    total_return_pct = float(data.get("total_return_pct", 0.0))
    max_dd = float(data.get("max_drawdown_pct", 0.0))
    events = data.get("events", [])
    phase_changes = data.get("phase_changes", [])
    buckets = data.get("capital_buckets", {})
    open_positions = data.get("open_positions", {})

    by_type = Counter(e.get("event_type", "?") for e in events)
    by_symbol = Counter(e.get("symbol", "?") for e in events)

    pnl_by_type = defaultdict(float)
    pnl_by_symbol = defaultdict(float)
    slip_by_type = defaultdict(float)
    slip_by_symbol = defaultdict(float)
    tp_pnl_by_symbol = defaultdict(float)
    gc_pnl_by_symbol = defaultdict(float)
    unwind_pnl_by_symbol = defaultdict(float)

    for e in events:
        et = e.get("event_type", "?")
        sym = e.get("symbol", "?")
        pnl = float(e.get("pnl", 0.0)) - float(e.get("fee", 0.0))
        pnl_by_type[et] += pnl
        pnl_by_symbol[sym] += pnl
        slip = float(e.get("slip_pips", 0.0))
        slip_by_type[et] += slip
        slip_by_symbol[sym] += slip

        if et == "TP_EXIT":
            tp_pnl_by_symbol[sym] += pnl
        elif et == "GARBAGE_COLLECT":
            gc_pnl_by_symbol[sym] += pnl
        elif et == "RISK_UNWIND":
            unwind_pnl_by_symbol[sym] += pnl

    avg_slip_by_type = {
        k: (slip_by_type[k] / by_type[k]) if by_type[k] else 0.0
        for k in by_type
    }
    avg_slip_by_symbol = {
        k: (slip_by_symbol[k] / by_symbol[k]) if by_symbol[k] else 0.0
        for k in by_symbol
    }

    phase_counter = Counter((r.get("from"), r.get("to")) for r in phase_changes)

    unrealized = final_equity - final_balance

    print("=" * 80)
    print("BACKTEST FORENSICS")
    print("=" * 80)
    print(f"Initial balance:         {initial_balance:.4f}")
    print(f"Final balance:           {final_balance:.4f}")
    print(f"Final equity:            {final_equity:.4f}")
    print(f"Unrealized at end:       {unrealized:.4f}")
    print(f"Total return:            {total_return_pct:.4f}%")
    print(f"Max drawdown:            {max_dd:.4f}%")
    print(f"Event count:             {len(events)}")

    print("\n--- Event Mix ---")
    print(dict(by_type))

    print("\n--- PnL by Event Type (realized net) ---")
    for k, v in sorted(pnl_by_type.items(), key=lambda x: x[0]):
        print(f"{k:16s} {v:>12.4f}")

    print("\n--- Avg Slippage (pips) by Event Type ---")
    for k, v in sorted(avg_slip_by_type.items(), key=lambda x: x[0]):
        print(f"{k:16s} {v:>12.4f}")

    print("\n--- PnL by Symbol (realized net from events) ---")
    for k, v in sorted(pnl_by_symbol.items(), key=lambda x: x[0]):
        print(f"{k:16s} {v:>12.4f}")

    print("\n--- Symbol Decomposition ---")
    symbols = sorted(set(by_symbol.keys()) | set(open_positions.keys()) | set(buckets.keys()))
    for sym in symbols:
        tp = tp_pnl_by_symbol.get(sym, 0.0)
        gc = gc_pnl_by_symbol.get(sym, 0.0)
        uw = unwind_pnl_by_symbol.get(sym, 0.0)
        total = pnl_by_symbol.get(sym, 0.0)
        evs = by_symbol.get(sym, 0)
        slip = avg_slip_by_symbol.get(sym, 0.0)
        b = buckets.get(sym, {})
        print(
            f"{sym}: total={total:.4f}, TP={tp:.4f}, GC={gc:.4f}, UW={uw:.4f}, "
            f"events={evs}, avg_slip={slip:.4f}, buckets={b}"
        )

    print("\n--- Phase Transition Frequency ---")
    for (a, b), c in phase_counter.most_common(15):
        print(f"{a:20s} -> {b:20s} : {c}")

    print("\n--- Open Position Snapshot ---")
    for sym, plist in open_positions.items():
        side_counts = Counter(p.get("side", "?") for p in plist)
        reserves = sum(float(p.get("paired_reserve", 0.0) or 0.0) for p in plist)
        print(f"{sym}: open={len(plist)} sides={dict(side_counts)} paired_reserve_sum={reserves:.4f}")

    print("=" * 80)


if __name__ == "__main__":
    main()
