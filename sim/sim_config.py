from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class BacktestConfig:
    start: datetime
    end: datetime
    timeframe: str = "M5"
    initial_balance: float = 10_000.0
    leverage: float = 30.0
    lot_size: float = 0.01
    max_rungs_per_side: int = 6
    offset_ratio: float = 0.5
    step_atr_mult: float = 0.9
    step_spread_mult: float = 12.0
    min_step_ticks: int = 5
    recenter_move_steps: float = 1.5
    atr_period: int = 14
    adx_period: int = 14
    trend_fast_ema: int = 34
    trend_slow_ema: int = 89
    trend_on_adx: float = 25.0
    trend_off_adx: float = 20.0
    exhaustion_confirm_bars: int = 4
    slow_trend_bars: int = 5
    slow_trend_min_move_steps: float = 1.0
    max_spread_pips: float = 10.0
    max_margin_usage_pct: float = 90.0
    max_drawdown_pct: float = 50.0
    max_net_delta_lots: float = 10.0
    cleanup_close_count: int = 2
    risk_off_unwind_per_cycle: int = 2
    protect_oscillation_bank: bool = True
    paired_ledger_enabled: bool = True
    paired_risk_steps: float = 1.0
    start_with_anchor: bool = True
    anchor_lot_mult: float = 1.0
    commission_per_lot_per_side: float = 0.0
    long_swap_per_lot_per_day: float = 0.0
    short_swap_per_lot_per_day: float = 0.0
    base_slippage_pips: float = 0.1
    range_slippage_mult: float = 0.05
    trend_slippage_mult: float = 0.10
    random_seed: int = 42
    progress_every_steps: int = 2000

    # LLM Oracle Settings
    use_llm_oracle: bool = True
    llm_model: str = "gpt-5.2"
    llm_eval_interval_bars: int = 12  # Evaluate every 1 hour (if 5m bars)
