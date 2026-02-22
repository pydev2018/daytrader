from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

p = Path("logs/backtest_report.json")
d = json.loads(p.read_text(encoding="utf-8"))
ev = d.get("events", [])
pc = d.get("phase_changes", [])

count_by_type = Counter(e.get("event_type") for e in ev)
count_by_symbol = Counter(e.get("symbol") for e in ev)

pnl_by_type = defaultdict(float)
slip_by_type = defaultdict(float)
for e in ev:
    et = e.get("event_type")
    pnl_by_type[et] += float(e.get("pnl", 0.0)) - float(e.get("fee", 0.0))
    slip_by_type[et] += float(e.get("slip_pips", 0.0))

avg_slip_by_type = {
    k: (slip_by_type[k] / count_by_type[k]) if count_by_type[k] else 0.0
    for k in count_by_type
}

phase_counts = Counter((x.get("from"), x.get("to")) for x in pc)

print("final_equity", round(float(d.get("final_equity", 0.0)), 4))
print("return_pct", round(float(d.get("total_return_pct", 0.0)), 4))
print("max_drawdown_pct", round(float(d.get("max_drawdown_pct", 0.0)), 4))
print("events_total", len(ev))
print("event_count_by_type", dict(count_by_type))
print("event_count_by_symbol", dict(count_by_symbol))
print("pnl_by_event_type", {k: round(v, 4) for k, v in pnl_by_type.items()})
print("avg_slip_pips_by_event_type", {k: round(v, 4) for k, v in avg_slip_by_type.items()})
print("top_phase_transitions", phase_counts.most_common(12))
print("net_pnl_by_symbol", d.get("net_pnl_by_symbol"))
print("event_count_by_symbol_report", d.get("event_count_by_symbol"))
print("open_positions_count", {k: len(v) for k, v in d.get("open_positions", {}).items()})
