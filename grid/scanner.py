"""
Symbol Scanner — ranks instruments by grid-trading suitability.

v2: Recalibrated for real OANDA data.
  - Hurst scoring adjusted (real FX is 0.5-0.7, not 0-1)
  - Added: round-trip profitability estimate (most important metric)
  - Added: fill frequency estimate (how many trades per day?)
  - Reweighted: profitability and fill frequency dominate scoring
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from brokers.mt5 import MT5Broker
from config import settings as cfg
from grid.regime import compute_trend_z
from utils.logger import get_logger

log = get_logger("scanner")


@dataclass
class SymbolScore:
    """Scored result for one symbol."""
    symbol: str
    score: float                  # 0-100 composite score
    verdict: str                  # EXCELLENT / GOOD / MARGINAL / AVOID
    mean_reversion: float         # 0-1 (higher = more mean-reverting)
    cost_efficiency: float        # 0-1 (higher = cheaper to trade)
    vol_regime: float             # 0-1 (moderate vol scores highest)
    spread_stability: float       # 0-1 (stable spreads = higher)
    trend_absence: float          # 0-1 (no trend = higher)
    details: dict = field(default_factory=dict)


# ─── Hurst exponent estimation (simplified rescaled range) ────────────────

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
            R = np.max(deviations) - np.min(deviations)
            S = np.std(chunk, ddof=1)
            if S > 0:
                rs_list.append(R / S)

        if rs_list:
            lags.append(lag)
            rs_values.append(np.mean(rs_list))

    if len(lags) < 3:
        return 0.5

    log_lags = np.log(lags)
    log_rs = np.log(rs_values)
    coeffs = np.polyfit(log_lags, log_rs, 1)
    H = coeffs[0]

    return max(0.0, min(1.0, H))


# ─── Variance ratio test ─────────────────────────────────────────────────

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


# ─── Oscillation amplitude estimator ─────────────────────────────────────

def _oscillation_vs_spread(close_prices: np.ndarray, spread: float,
                           window: int = 20) -> float:
    """Estimate how many spreads worth of oscillation exists per window.

    This is the most direct measure of grid profitability:
    if price oscillates 10 pips and spread is 1 pip, there's 10x
    the movement relative to cost = lots of room for the grid.

    Returns the ratio: median_oscillation / spread.
    """
    if len(close_prices) < window + 1 or spread <= 0:
        return 0.0

    oscillations = []
    for i in range(window, len(close_prices)):
        chunk = close_prices[i - window:i]
        # Oscillation = sum of absolute bar-to-bar moves
        moves = np.abs(np.diff(chunk))
        total_move = np.sum(moves)
        # Net move = absolute distance start to end
        net_move = abs(chunk[-1] - chunk[0])
        # Oscillation above trend = total path minus straight line
        excess = total_move - net_move
        oscillations.append(excess)

    median_osc = np.median(oscillations)
    return median_osc / spread


# ─── Fill frequency estimator ────────────────────────────────────────────

def _estimate_fills_per_day(close_prices: np.ndarray, spacing: float,
                            bars_per_day: float) -> float:
    """Estimate how many grid level crossings happen per day.

    Counts how many times price crosses a spacing-width band per bar,
    then scales to daily.
    """
    if len(close_prices) < 10 or spacing <= 0:
        return 0.0

    crossings = 0
    for i in range(1, len(close_prices)):
        move = abs(close_prices[i] - close_prices[i - 1])
        crossings += move / spacing

    crossings_per_bar = crossings / len(close_prices)
    return crossings_per_bar * bars_per_day


class GridScanner:
    """Scans and ranks symbols for grid trading suitability.

    v3 weights — calibrated against real OANDA backtest results:
    - Cost efficiency is king (low spread/ATR = profitable)
    - Mean-reversion quality matters more than raw oscillation amplitude
    - JPY pairs are auto-penalized (3-digit pricing, different scale)
    - Oscillation is weighted by mean-reversion to avoid rewarding trending vol
    """

    # Score weights (sum to 1.0)
    W_MEAN_REVERSION = 0.25    # increased — this separates winners from losers
    W_COST_EFFICIENCY = 0.30   # still high — cost is the edge
    W_VOL_REGIME = 0.10
    W_SPREAD_STABILITY = 0.10
    W_TREND_ABSENCE = 0.10
    W_OSCILLATION = 0.15       # reduced — raw amplitude misleads on JPY/trending

    def __init__(self, broker: MT5Broker):
        self.broker = broker

    def scan(
        self,
        symbols: list[str] | None = None,
        timeframe: str = "M5",
        bar_count: int = 500,
    ) -> list[SymbolScore]:
        symbols = symbols or cfg.GRID_SYMBOLS
        results: list[SymbolScore] = []

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
    ) -> Optional[SymbolScore]:
        if not self.broker.select_symbol(symbol):
            return None

        sym_info = self.broker.symbol_info(symbol)
        if sym_info is None:
            return None

        tick = self.broker.symbol_tick(symbol)
        if tick is None:
            return None

        df = self.broker.get_rates(symbol, timeframe, bar_count)
        if df is None or len(df) < 50:
            return None

        bid = tick.get("bid", 0.0)
        ask = tick.get("ask", 0.0)
        if bid <= 0 or ask <= 0:
            return None

        mid = (bid + ask) / 2.0
        spread = ask - bid
        point = sym_info.get("point", 0.00001)
        tick_size = sym_info.get("trade_tick_size", point)
        digits = sym_info.get("digits", 5)

        close_prices = df["close"].values.astype(float)
        high_prices = df["high"].values.astype(float)
        low_prices = df["low"].values.astype(float)

        log_returns = np.diff(np.log(close_prices))
        if len(log_returns) < 20:
            return None

        # ── 1. Mean Reversion Score ─────────────────────────────────────
        hurst = _hurst_exponent(close_prices)
        vr = _variance_ratio(log_returns, lag=5)

        # Recalibrated for real FX: H typically 0.50-0.70
        # H=0.50 (random walk) → score 0.5, H=0.45 → 1.0, H=0.65 → 0.0
        hurst_score = max(0.0, min(1.0, (0.65 - hurst) / 0.20))
        vr_score = max(0.0, min(1.0, (1.0 - vr) / 0.3))
        mean_reversion = 0.5 * hurst_score + 0.5 * vr_score

        # ── 2. Cost Efficiency Score ────────────────────────────────────
        tr = np.maximum(
            high_prices[1:] - low_prices[1:],
            np.maximum(
                np.abs(high_prices[1:] - close_prices[:-1]),
                np.abs(low_prices[1:] - close_prices[:-1]),
            ),
        )
        atr = np.mean(tr[-50:]) if len(tr) >= 50 else np.mean(tr)

        cost_ratio = spread / max(atr, 1e-10)
        # Calibrated: < 5% = great, 5-15% = ok, > 30% = bad
        cost_efficiency = max(0.0, min(1.0, (0.30 - cost_ratio) / 0.25))

        # ── 3. Volatility Regime Score ──────────────────────────────────
        ann_vol = np.std(log_returns) * math.sqrt(252 * (1440 / 5))

        if ann_vol < 0.03:
            vol_regime = ann_vol / 0.03
        elif ann_vol <= 0.15:
            vol_regime = 1.0
        elif ann_vol <= 0.30:
            vol_regime = 1.0 - (ann_vol - 0.15) / 0.15
        else:
            vol_regime = 0.0

        # ── 4. Spread Stability Score ───────────────────────────────────
        if "spread" in df.columns:
            bar_spreads = df["spread"].values.astype(float) * point
            spread_cv = np.std(bar_spreads) / max(np.mean(bar_spreads), 1e-10)
        else:
            bar_spreads_proxy = (high_prices - low_prices) / np.maximum(close_prices, 1e-10)
            spread_cv = np.std(bar_spreads_proxy) / max(np.mean(bar_spreads_proxy), 1e-10)
        spread_stability = max(0.0, min(1.0, 1.0 - spread_cv / 2.0))

        # ── 5. Trend Absence Score ──────────────────────────────────────
        recent_prices = close_prices[-min(200, len(close_prices)):]
        trend_z = compute_trend_z(recent_prices.tolist())
        trend_absence = max(0.0, min(1.0, 1.0 - abs(trend_z) / 3.0))

        # ── 6. Oscillation Score ──────────────────────────────────────
        osc_ratio = _oscillation_vs_spread(close_prices, spread, window=20)
        # Weight oscillation by mean-reversion quality:
        # High oscillation on a trending pair is DANGEROUS, not good.
        # Only reward oscillation when it's mean-reverting oscillation.
        mr_weight = max(0.1, mean_reversion)  # floor at 0.1
        oscillation_score = max(0.0, min(1.0, osc_ratio / 25.0)) * mr_weight

        # Estimate fills per day (M5 bars)
        sigma = np.std(log_returns)
        approx_spacing = max(
            cfg.GRID_SPACING_K_SIGMA * mid * sigma * math.sqrt(cfg.VOL_HORIZON_SECONDS / 300),
            cfg.GRID_SPACING_K_COST * (spread + cfg.SLIPPAGE_BUFFER_TICKS * tick_size),
            cfg.GRID_SPACING_MIN_TICKS * tick_size,
        )
        fills_per_day = _estimate_fills_per_day(close_prices, approx_spacing, 288)

        # ── JPY pair penalty ──────────────────────────────────────────
        # JPY pairs have 3-digit pricing (150.500 vs 1.08500).
        # Our grid parameters are calibrated for 5-digit pairs.
        # Until per-symbol parameter scaling is implemented, penalize JPY.
        is_jpy = "JPY" in symbol.upper()
        jpy_penalty = 0.5 if is_jpy else 1.0

        # ── Composite Score ─────────────────────────────────────────────
        composite = (
            self.W_MEAN_REVERSION * mean_reversion
            + self.W_COST_EFFICIENCY * cost_efficiency
            + self.W_VOL_REGIME * vol_regime
            + self.W_SPREAD_STABILITY * spread_stability
            + self.W_TREND_ABSENCE * trend_absence
            + self.W_OSCILLATION * oscillation_score
        ) * 100 * jpy_penalty

        if composite >= 65:
            verdict = "EXCELLENT"
        elif composite >= 45:
            verdict = "GOOD"
        elif composite >= 30:
            verdict = "MARGINAL"
        else:
            verdict = "AVOID"

        return SymbolScore(
            symbol=symbol,
            score=round(composite, 1),
            verdict=verdict,
            mean_reversion=round(mean_reversion, 3),
            cost_efficiency=round(cost_efficiency, 3),
            vol_regime=round(vol_regime, 3),
            spread_stability=round(spread_stability, 3),
            trend_absence=round(trend_absence, 3),
            details={
                "hurst": round(hurst, 4),
                "variance_ratio": round(vr, 4),
                "spread_pips": round(spread / point, 1),
                "atr_pips": round(atr / point, 1),
                "cost_ratio_pct": round(cost_ratio * 100, 2),
                "ann_vol_pct": round(ann_vol * 100, 2),
                "trend_z": round(trend_z, 2),
                "spread_cv": round(spread_cv, 3),
                "oscillation_ratio": round(osc_ratio, 1),
                "est_fills_per_day": round(fills_per_day, 1),
                "est_spacing_pips": round(approx_spacing / point, 1),
                "oscillation_score": round(oscillation_score, 3),
            },
        )


# ─── Default FX universe ─────────────────────────────────────────────────

FX_MAJORS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "USDCAD", "NZDUSD",
]

FX_CROSSES = [
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD",
    "CADJPY", "CADCHF",
    "CHFJPY",
]

FX_UNIVERSE = FX_MAJORS + FX_CROSSES
