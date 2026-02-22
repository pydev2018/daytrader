from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Phase(str, Enum):
    INIT = "INIT"
    RANGE = "RANGE"
    TREND_LOCK = "TREND_LOCK"
    EXHAUSTION_CONFIRM = "EXHAUSTION_CONFIRM"
    GARBAGE_COLLECT = "GARBAGE_COLLECT"
    RECENTER = "RECENTER"
    RISK_OFF = "RISK_OFF"


class TrendDirection(str, Enum):
    FLAT = "FLAT"
    UP = "UP"
    DOWN = "DOWN"


@dataclass
class SymbolRuntime:
    symbol: str
    phase: Phase = Phase.INIT
    center_price: float = 0.0
    step_ticks: int = 0
    trend: TrendDirection = TrendDirection.FLAT
    stable_bars: int = 0
    chop_pnl: float = 0.0
    buffer_pnl: float = 0.0
    last_mid: float = 0.0
    anchor_initialized: bool = False
    trend_persist_bars: int = 0
    metadata: dict[str, float | int | str] = field(default_factory=dict)
