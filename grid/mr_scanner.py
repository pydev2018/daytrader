"""
Mean Reversion Scanner — ranks symbols by intraday oscillation quality.

Focus:
- Mean reversion strength (Hurst, variance ratio, OU half-life)
- Cost efficiency (ATR vs spread)
- Oscillation frequency (mean crossings)
- Trend absence (avoid trending pairs)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from brokers.mt5 import MT5Broker
from grid.regime import compute_trend_z
from utils.logger import get_logger

log = get_logger("mr_scanner")


@dataclass
class MRScore:
    symbol: str
    score: float
    verdict: str
    mean_reversion: float
    cost_efficiency: float
    trend_absence: float
    oscillation: float
    crossings: float
    details: dict = field(default_factory=dict)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _timeframe_minutes(tf: str) -> int:
    tf = tf.upper().strip()
    if tf.startswith("M"):
        return max(1, int(tf[1:]))
    if tf.startswith("H"):
        return max(1, int(tf[1:]) * 60)
    if tf.startswith("D"):
        return max(1, int(tf[1:]) * 60 * 24)
    return 5


def _hurst_exponent(prices: np.ndarray, max_lag: int = 40) -> float:
    """Estimate Hurst exponent via rescaled range (R/S) method."""
    if len(prices) < max_lag * 2:
        return 0.5

    log_prices = np.log(prices)
    returns = np.diff(log_prices)

    lags = []
    rs_values = []

    for lag in range(10, max_lag + 1, 2):
        n_chunks = len(returns) // lag
        if n_chunks < 2:
            continue

        rs_list = []
        for i in range(n_chunks):
            chunk = returns[i * lag:(i + 1) * lag]
            mean_r = np.mean(chunk)
            deviations = np.cumsum(chunk - mean_r)
            r = np.max(deviations) - np.min(deviations)
            s = np.std(chunk, ddof=1)
            if s > 0:
                rs_list.append(r / s)

        if rs_list:
            lags.append(lag)
            rs_values.append(np.mean(rs_list))

    if len(lags) < 3:
        return 0.5

    log_lags = np.log(lags)
    log_rs = np.log(rs_values)
    coeffs = np.polyfit(log_lags, log_rs, 1)
    h = coeffs[0]
    return max(0.0, min(1.0, h))


def _variance_ratio(returns: np.ndarray, lag: int = 5) -> float:
    """Variance ratio VR(lag). VR < 1 = mean-reverting."""
    if len(returns) < lag * 3:
        return 1.0

    var1 = np.var(returns, ddof=1)
    if var1 < 1e-20:
        return 1.0

    cumret = np.cumsum(returns)
    lag_rets = cumret[lag:] - np.concatenate([[0], cumret[:-lag]])[1:]
    if len(lag_rets) < 5:
        return 1.0

    var_lag = np.var(lag_rets, ddof=1)
    return var_lag / (lag * var1) if var1 > 0 else 1.0


def _half_life(residual: np.ndarray) -> float:
    """Estimate OU half-life (in bars) from a residual series."""
    if len(residual) < 50:
        return float("inf")
    x = residual - np.mean(residual)
    x_lag = x[:-1]
    x_next = x[1:]
    try:
        b, a = np.polyfit(x_lag, x_next, 1)
    except Exception:
        return float("inf")
    if b <= 0:
        return 1.0  # very fast mean reversion
    if b >= 0.999:
        return float("inf")
    return float(-math.log(2) / math.log(b))


def _mean_crossings_per_hour(residual: np.ndarray, bars_per_hour: float) -> float:
    if len(residual) < 10 or bars_per_hour <= 0:
        return 0.0
    sign = np.sign(residual)
    crossings = np.sum((sign[1:] * sign[:-1]) < 0)
    hours = len(residual) / bars_per_hour
    return crossings / max(hours, 1e-6)


def _oscillation_vs_spread(close_prices: np.ndarray, spread: float,
                           window: int = 20) -> float:
    """Median oscillation amplitude vs spread."""
    if len(close_prices) < window + 1 or spread <= 0:
        return 0.0
    oscillations = []
    for i in range(window, len(close_prices)):
        chunk = close_prices[i - window:i]
        moves = np.abs(np.diff(chunk))
        total_move = np.sum(moves)
        net_move = abs(chunk[-1] - chunk[0])
        excess = total_move - net_move
        oscillations.append(excess)
    median_osc = np.median(oscillations)
    return median_osc / spread


class MeanReversionScanner:
    """Scan and rank symbols by intraday mean-reversion quality."""

    # Weights sum to 1.0
    W_MEAN_REVERSION = 0.35
    W_COST_EFFICIENCY = 0.30
    W_TREND_ABSENCE = 0.15
    W_OSCILLATION = 0.10
    W_CROSSINGS = 0.10

    def __init__(self, broker: MT5Broker):
        self.broker = broker

    def scan(
        self,
        symbols: list[str],
        timeframe: str = "M5",
        bar_count: int = 2000,
    ) -> list[MRScore]:
        results: list[MRScore] = []
        for symbol in symbols:
            try:
                score = self._score_symbol(symbol, timeframe, bar_count)
                if score is not None:
                    results.append(score)
            except Exception as exc:
                log.warning(f"Failed to scan {symbol}: {exc}")
        results.sort(key=lambda s: s.score, reverse=True)
        return results

    def _score_symbol(
        self, symbol: str, timeframe: str, bar_count: int
    ) -> Optional[MRScore]:
        if not self.broker.select_symbol(symbol):
            return None

        sym_info = self.broker.symbol_info(symbol)
        if sym_info is None:
            return None

        tick = self.broker.symbol_tick(symbol) or {}
        df = self.broker.get_rates(symbol, timeframe, bar_count)
        if df is None or len(df) < 200:
            return None

        point = sym_info.get("point", 0.00001)
        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", 0.0))
        live_spread = ask - bid if bid > 0 and ask > 0 else 0.0

        if "spread" in df.columns:
            spread_series = df["spread"].values.astype(float) * point
            median_spread = float(np.median(spread_series))
        else:
            median_spread = 0.0

        spread = median_spread if median_spread > 0 else live_spread
        if spread <= 0:
            return None

        close_prices = df["close"].values.astype(float)
        high_prices = df["high"].values.astype(float)
        low_prices = df["low"].values.astype(float)

        log_returns = np.diff(np.log(close_prices))
        if len(log_returns) < 50:
            return None

        # ATR (price units)
        tr = np.maximum(
            high_prices[1:] - low_prices[1:],
            np.maximum(
                np.abs(high_prices[1:] - close_prices[:-1]),
                np.abs(low_prices[1:] - close_prices[:-1]),
            ),
        )
        atr = float(np.mean(tr[-50:])) if len(tr) >= 50 else float(np.mean(tr))

        # Residual vs slow mean (EMA)
        ema = pd.Series(close_prices).ewm(span=200, adjust=False).mean().values
        residual = close_prices - ema

        # Mean reversion metrics
        hurst = _hurst_exponent(close_prices)
        vr = _variance_ratio(log_returns, lag=5)
        half_life = _half_life(residual)

        hurst_score = _clamp((0.65 - hurst) / 0.20)
        vr_score = _clamp((1.0 - vr) / 0.30)
        if math.isfinite(half_life):
            if half_life <= 10:
                hl_score = 1.0
            elif half_life <= 40:
                hl_score = 1.0 - (half_life - 10.0) / 30.0
            else:
                hl_score = 0.0
        else:
            hl_score = 0.0
        mean_reversion = (
            0.4 * hurst_score + 0.3 * vr_score + 0.3 * hl_score
        )

        # Cost efficiency
        atr_spread_ratio = atr / max(spread, 1e-12)
        cost_efficiency = _clamp((atr_spread_ratio - 4.0) / 8.0)

        # Trend absence
        recent_prices = close_prices[-min(200, len(close_prices)):]
        trend_z = compute_trend_z(recent_prices.tolist())
        trend_absence = _clamp(1.0 - abs(trend_z) / 3.0)

        # Oscillation & crossings
        osc_ratio = _oscillation_vs_spread(close_prices, spread, window=20)
        oscillation = _clamp(osc_ratio / 20.0)
        bars_per_hour = 60.0 / _timeframe_minutes(timeframe)
        crossings = _mean_crossings_per_hour(residual, bars_per_hour)
        crossings_score = _clamp(crossings / 4.0)

        composite = (
            self.W_MEAN_REVERSION * mean_reversion
            + self.W_COST_EFFICIENCY * cost_efficiency
            + self.W_TREND_ABSENCE * trend_absence
            + self.W_OSCILLATION * oscillation
            + self.W_CROSSINGS * crossings_score
        ) * 100.0

        if composite >= 70:
            verdict = "EXCELLENT"
        elif composite >= 55:
            verdict = "GOOD"
        elif composite >= 40:
            verdict = "OK"
        else:
            verdict = "AVOID"

        return MRScore(
            symbol=symbol,
            score=round(composite, 1),
            verdict=verdict,
            mean_reversion=round(mean_reversion, 3),
            cost_efficiency=round(cost_efficiency, 3),
            trend_absence=round(trend_absence, 3),
            oscillation=round(oscillation, 3),
            crossings=round(crossings, 2),
            details={
                "hurst": round(hurst, 4),
                "variance_ratio": round(vr, 4),
                "half_life_bars": round(half_life, 1) if math.isfinite(half_life) else float("inf"),
                "atr_pips": round(atr / point, 1),
                "spread_pips": round(spread / point, 1),
                "atr_spread_ratio": round(atr_spread_ratio, 2),
                "trend_z": round(trend_z, 2),
                "osc_ratio": round(osc_ratio, 1),
                "crossings_per_hour": round(crossings, 2),
                "live_spread_pips": round(live_spread / point, 1) if live_spread > 0 else 0.0,
            },
        )
