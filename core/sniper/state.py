"""
M15 Sniper data model and state store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


Direction = Literal["BUY", "SELL"]
Regime = Literal["trend", "range", "transition"]
TriggerMode = Literal["reclaim", "rejection", "break", "retest"]
EntryType = Literal["market", "pending_stop", "pending_limit"]
SetupType = Literal["TPR", "RBH", "ECR"]


@dataclass
class PivotPoint:
    type: Literal["high", "low"]
    idx: int
    time: int  # epoch seconds
    price: float


@dataclass
class M15Snapshot:
    symbol: str
    bars_count: int
    forming_bar_time: int
    atr14: float
    ema20: float
    ema50: float
    ema20_slope: float
    ema50_slope: float
    pivots: list[PivotPoint] = field(default_factory=list)
    trend_state: Regime = "transition"
    range_high: float = 0.0
    range_low: float = 0.0
    range_width: float = 0.0
    touch_count_high: int = 0
    touch_count_low: int = 0
    compression_pct: float = 0.0
    major_levels: list[float] = field(default_factory=list)
    spread_pips: float = 0.0
    spread_atr_ratio: float = 0.0


@dataclass
class FastCandidate:
    symbol: str
    regime: Literal["trend", "range", "transition"]
    bias: Literal["long", "short", "neutral"]
    atr: float
    spread_atr_ratio: float
    quick_score: float
    regime_confidence: float = 0.0
    trend_conf: float = 0.0
    range_conf: float = 0.0
    gates: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


@dataclass
class TPRSetupState:
    symbol: str
    direction: Direction
    detected_at_time: int
    expires_at_bar: int
    swing_low_recent: float = 0.0
    swing_low_prev: float = 0.0
    swing_high_recent: float = 0.0
    pullback_start_time: int = 0
    pullback_low: float = 0.0
    pullback_high: float = 0.0
    pb_high: float = 0.0
    in_pullback_zone: bool = False
    structure_intact: bool = True
    invalidated: bool = False
    trigger_level: float = 0.0
    trigger_mode: TriggerMode = "reclaim"
    atr: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    no_chase_max: float = 0.0
    confidence: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass
class RBHSetupState:
    symbol: str
    direction: Direction
    detected_at_time: int
    expires_at_bar: int
    range_high: float
    range_low: float
    range_width: float
    touch_count_high: int
    touch_count_low: int
    compression_ok: bool
    break_state: Literal["active", "expired", "invalid"] = "active"
    break_level: float = 0.0
    break_candle_high: float = 0.0
    break_time: int = 0
    retest_window_end: int = 0
    retest_confirmed: bool = False
    retest_type: TriggerMode = "retest"
    atr: float = 0.0
    entry: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    no_chase_max: float = 0.0
    confidence: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass
class ECRSetupState:
    symbol: str
    direction: Direction
    detected_at_time: int
    expires_at_bar: int
    trend_cross_time: int = 0
    cycle_cross_count: int = 0
    entry_price: float = 0.0
    trigger_level: float = 0.0
    atr: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    no_chase_max: float = 0.0
    confidence: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass
class SymbolState:
    symbol: str
    last_m15_bar_time: int = 0
    last_fast_pass_time: int = 0
    active_tpr: Optional[TPRSetupState] = None
    active_rbh: Optional[RBHSetupState] = None
    active_ecr: Optional[ECRSetupState] = None
    cooldowns: dict[str, int] = field(default_factory=dict)
    last_trigger_time: int = 0
    last_signal_time: int = 0
    last_price: float = 0.0
    regime: str = "transition"
    regime_streak: int = 0
    regime_confidence: float = 0.0


@dataclass
class TriggerEvent:
    setup_type: SetupType
    symbol: str
    direction: Direction
    trigger_time: int
    trigger_price: float
    momentum_score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class ExecutionIntent:
    setup_type: SetupType
    symbol: str
    direction: Direction
    entry_type: EntryType
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    expiry_bar: int
    risk_factor: float
    atr: float = 0.0
    trigger_level: float = 0.0
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
