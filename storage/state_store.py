from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from strategy.types import Phase, SymbolRuntime, TrendDirection


def load_state(path: Path) -> dict[str, SymbolRuntime]:
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, SymbolRuntime] = {}
    for symbol, data in raw.items():
        out[symbol] = SymbolRuntime(
            symbol=symbol,
            phase=Phase(data.get("phase", Phase.INIT.value)),
            center_price=float(data.get("center_price", 0.0)),
            step_ticks=int(data.get("step_ticks", 0)),
            trend=TrendDirection(data.get("trend", TrendDirection.FLAT.value)),
            stable_bars=int(data.get("stable_bars", 0)),
            chop_pnl=float(data.get("chop_pnl", 0.0)),
            buffer_pnl=float(data.get("buffer_pnl", 0.0)),
            last_mid=float(data.get("last_mid", 0.0)),
            anchor_initialized=bool(data.get("anchor_initialized", False)),
            trend_persist_bars=int(data.get("trend_persist_bars", 0)),
            metadata=dict(data.get("metadata", {})),
        )
    return out


def save_state(path: Path, states: dict[str, SymbolRuntime]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        symbol: {
            **asdict(state),
            "phase": state.phase.value,
            "trend": state.trend.value,
        }
        for symbol, state in states.items()
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
