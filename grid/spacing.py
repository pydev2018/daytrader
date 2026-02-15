"""
Spacing calculation and volatility estimation.

Fixes:
- S1 #6: VolEstimator now uses time-aware EWMA decay (like EmaAnchor).
  Lambda is scaled by dt so vol estimates are consistent regardless of
  tick arrival rate. This prevents over-counting vol in fast markets
  and under-counting in slow markets.
"""

from __future__ import annotations

import math
import time


class VolEstimator:
    """Time-aware EWMA volatility of log returns.

    The decay factor is adjusted for the actual elapsed time between updates:
        effective_lambda = lambda ^ (dt / reference_dt)

    where reference_dt is the expected step interval (default 1.0 seconds).
    This ensures the effective half-life is consistent regardless of update
    frequency.
    """

    def __init__(self, lam: float, reference_dt: float = 1.0):
        self.lam = float(lam)
        self.reference_dt = max(0.01, float(reference_dt))
        self._last_price: float | None = None
        self._last_ts: float | None = None
        self.sigma2: float = 0.0

    def update(self, price: float, ts: float | None = None) -> float:
        ts = ts if ts is not None else time.time()
        if self._last_price is None or self._last_price <= 0:
            self._last_price = float(price)
            self._last_ts = ts
            return 0.0

        if price <= 0:
            return math.sqrt(self.sigma2)

        # Fix: use explicit None check — 0.0 is a valid timestamp
        last_ts = self._last_ts if self._last_ts is not None else ts
        dt = max(0.001, ts - last_ts)
        r = math.log(price / self._last_price)

        # Time-adjusted decay: scale lambda by dt relative to reference
        # This makes the effective half-life independent of update frequency
        effective_lam = self.lam ** (dt / self.reference_dt)
        self.sigma2 = effective_lam * self.sigma2 + (1 - effective_lam) * (r * r)

        self._last_price = float(price)
        self._last_ts = ts
        return math.sqrt(self.sigma2)

    @property
    def sigma(self) -> float:
        return math.sqrt(self.sigma2)


def compute_spacing(
    mid: float,
    spread: float,
    tick_size: float,
    sigma: float,
    step_seconds: float,
    horizon_seconds: float,
    k_sigma: float,
    k_cost: float,
    min_ticks: int,
    slip_ticks: int,
) -> tuple[float, float]:
    """
    Return (spacing, cost_floor) in price units.

    spacing = max(k_sigma * vol_move, k_cost * cost_floor, min_ticks * tick_size)

    The vol_move is sigma scaled from step_seconds to horizon_seconds via
    square-root-of-time.
    """
    slip_buf = slip_ticks * tick_size
    cost_floor = spread + slip_buf
    if step_seconds <= 0:
        step_seconds = 1.0

    # Scale sigma to the horizon
    sigma_h = sigma * math.sqrt(max(horizon_seconds, 1.0) / step_seconds)
    vol_move = max(0.0, mid * sigma_h)

    spacing = max(
        k_sigma * vol_move,
        k_cost * cost_floor,
        min_ticks * tick_size,
    )
    if tick_size > 0:
        spacing = round(spacing / tick_size) * tick_size
    return spacing, cost_floor
