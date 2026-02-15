"""
Anchor estimation (EMA) for grid center.

Fix: _last_ts uses `is None` check instead of falsy `or` operator.
     The original `self._last_ts or ts` treated timestamp 0.0 as falsy,
     causing dt=0 and the anchor to never update in backtests.
"""

from __future__ import annotations

import math
import time


class EmaAnchor:
    """EMA anchor with time-aware decay (half-life in seconds)."""

    def __init__(self, half_life_seconds: float):
        self.half_life_seconds = max(1.0, float(half_life_seconds))
        self.value: float | None = None
        self._last_ts: float | None = None

    def update(self, price: float, ts: float | None = None) -> float:
        ts = ts if ts is not None else time.time()
        if self.value is None:
            self.value = float(price)
            self._last_ts = ts
            return self.value

        # Fix: use explicit None check — 0.0 is a valid timestamp
        last_ts = self._last_ts if self._last_ts is not None else ts
        dt = max(0.0, ts - last_ts)
        if dt <= 0:
            return self.value

        # Time-aware EMA: alpha derived from half-life
        alpha = 1.0 - math.exp(-math.log(2) * dt / self.half_life_seconds)
        self.value = (1 - alpha) * self.value + alpha * price
        self._last_ts = ts
        return self.value
