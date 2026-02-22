from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class SymbolSpec:
    point: float
    pip_size: float
    contract_size: float
    pip_value_per_lot: float


@dataclass
class PendingOrder:
    symbol: str
    side: str
    price: float
    tp: float
    volume: float
    active_from: datetime


@dataclass
class Position:
    symbol: str
    side: str
    entry_price: float
    tp: float
    volume: float
    opened_at: datetime
    is_anchor: bool = False
    entry_phase: str = "RANGE"
    paired_reserve: float = 0.0


@dataclass
class FillEvent:
    timestamp: datetime
    symbol: str
    side: str
    event_type: str
    price: float
    volume: float
    pnl: float
    fee: float
    slip_pips: float
