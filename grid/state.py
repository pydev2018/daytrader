"""
Grid state models and persistence helpers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings as cfg
from utils.logger import get_logger

log = get_logger("grid_state")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GridRung:
    rung_id: str
    level_index: int
    entry_side: str  # BUY or SELL
    state: str  # ENTRY or EXIT
    entry_price: float
    size: float
    fill_price: float | None = None
    exit_price: float | None = None
    order_ticket: int | None = None
    last_update: str = ""


@dataclass
class GridState:
    symbol: str
    grid_id: str
    center: float
    anchor: float
    spacing: float
    levels: int
    last_reset_ts: float
    last_update_ts: float
    rungs: list[GridRung]
    last_deal_time: str
    inventory_lots: float
    paused: bool = False
    pause_reason: str = ""


def _rung_from_dict(data: dict[str, Any]) -> GridRung:
    # H1: Filter to known fields only — prevents crash if state file
    # has extra keys from a previous version (schema migration safety).
    known_fields = set(GridRung.__dataclass_fields__)
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return GridRung(**filtered)


def _state_from_dict(data: dict[str, Any]) -> GridState:
    rungs = [_rung_from_dict(r) for r in data.get("rungs", [])]
    return GridState(
        symbol=data.get("symbol", ""),
        grid_id=data.get("grid_id", ""),
        center=data.get("center", 0.0),
        anchor=data.get("anchor", 0.0),
        spacing=data.get("spacing", 0.0),
        levels=data.get("levels", 0),
        last_reset_ts=data.get("last_reset_ts", 0.0),
        last_update_ts=data.get("last_update_ts", 0.0),
        rungs=rungs,
        last_deal_time=data.get("last_deal_time", ""),
        inventory_lots=data.get("inventory_lots", 0.0),
        paused=data.get("paused", False),
        pause_reason=data.get("pause_reason", ""),
    )


def load_state(path: Path | None = None) -> dict[str, GridState]:
    """Load grid state from disk (symbol -> GridState)."""
    path = path or cfg.GRID_STATE_PATH
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {sym: _state_from_dict(state) for sym, state in raw.items()}
    except Exception as exc:
        log.warning(f"Failed to load grid state: {exc}")
        return {}


def save_state(states: dict[str, GridState], path: Path | None = None):
    """Persist grid state atomically."""
    path = path or cfg.GRID_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {sym: asdict(state) for sym, state in states.items()}
    tmp_path = path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        tmp_path.replace(path)
    except Exception as exc:
        log.warning(f"Failed to save grid state: {exc}")


def new_state(symbol: str, grid_id: str) -> GridState:
    # H6: Set last_deal_time to 24 hours ago (not now) so that the first
    # detect_fills call scans recent deal history. This catches fills
    # that happened while the bot was down.
    from datetime import timedelta
    lookback = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    return GridState(
        symbol=symbol,
        grid_id=grid_id,
        center=0.0,
        anchor=0.0,
        spacing=0.0,
        levels=cfg.GRID_LEVELS,
        last_reset_ts=0.0,
        last_update_ts=0.0,
        rungs=[],
        last_deal_time=lookback,
        inventory_lots=0.0,
        paused=False,
        pause_reason="",
    )
