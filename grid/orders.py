"""
Grid rung creation and order specs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from grid.state import GridRung


@dataclass
class OrderSpec:
    symbol: str
    side: str  # BUY or SELL
    price: float
    volume: float
    comment: str
    rung_id: str
    state: str  # ENTRY or EXIT
    order_type: str = "LIMIT"


def make_comment(grid_id: str, rung_id: str, state: str) -> str:
    # Fix S2 #15: Both ENTRY and EXIT start with 'E', so we must check
    # for ENTRY explicitly. "EXIT" must map to "X", "ENTRY" to "E".
    short_state = "E" if state.upper() == "ENTRY" else "X"
    return f"g{grid_id}:{rung_id}:{short_state}"


def parse_comment(comment: str) -> tuple[str, str, str] | None:
    try:
        parts = comment.split(":")
        if len(parts) != 3 or not parts[0].startswith("g"):
            return None
        grid_id = parts[0][1:]
        rung_id = parts[1]
        state = "ENTRY" if parts[2] == "E" else "EXIT"
        return grid_id, rung_id, state
    except Exception:
        return None


def build_rungs(symbol: str, center: float, spacing: float, levels: int) -> list[GridRung]:
    rungs: list[GridRung] = []
    for idx in range(-levels, 0):
        rung_id = f"L{idx}"
        price = center + idx * spacing
        rungs.append(
            GridRung(
                rung_id=rung_id,
                level_index=idx,
                entry_side="BUY",
                state="ENTRY",
                entry_price=price,
                size=0.0,
            )
        )
    for idx in range(1, levels + 1):
        rung_id = f"L{idx}"
        price = center + idx * spacing
        rungs.append(
            GridRung(
                rung_id=rung_id,
                level_index=idx,
                entry_side="SELL",
                state="ENTRY",
                entry_price=price,
                size=0.0,
            )
        )
    return rungs


def update_rung_on_fill(
    rung: GridRung,
    fill_price: float,
    spacing: float,
    now_iso: str,
) -> GridRung:
    """Toggle rung state after a fill."""
    if rung.state == "ENTRY":
        rung.state = "EXIT"
        rung.fill_price = fill_price
        if rung.entry_side == "BUY":
            rung.exit_price = fill_price + spacing
        else:
            rung.exit_price = fill_price - spacing
    else:
        rung.state = "ENTRY"
        rung.fill_price = None
        rung.exit_price = None
    rung.last_update = now_iso
    return rung


def desired_orders(
    symbol: str,
    grid_id: str,
    rungs: list[GridRung],
    entry_sizes: dict[str, float],
    allow_entries: bool = True,
    vol_min: float = 0.01,
) -> list[OrderSpec]:
    """
    Build desired ENTRY orders from rungs.

    EXIT orders are no longer generated here. On hedging accounts,
    exits are handled by setting T/P directly on the position via
    TRADE_ACTION_SLTP. MT5 closes the position automatically.

    Fix C1: Orders with volume below vol_min are skipped entirely.
    """
    orders: list[OrderSpec] = []
    for rung in rungs:
        if rung.state == "ENTRY":
            if not allow_entries:
                continue
            size = entry_sizes.get(rung.rung_id, 0.0)
            if size < vol_min:
                continue
            rung.size = size
            price = rung.entry_price
            comment = make_comment(grid_id, rung.rung_id, "ENTRY")
            orders.append(
                OrderSpec(
                    symbol=symbol,
                    side=rung.entry_side,
                    price=price,
                    volume=size,
                    comment=comment,
                    rung_id=rung.rung_id,
                    state="ENTRY",
                )
            )
        # EXIT rungs: no orders generated. TP is set on the position itself.
    return orders
