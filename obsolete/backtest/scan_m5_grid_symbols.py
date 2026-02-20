"""
M5 Grid Symbol Selector
-----------------------
Ranks symbols by how well their recent M5 behavior matches this grid system:
- Choppy / mean-reverting movement
- Limited persistent trend runs
- Adequate movement vs spread/cost floor
- Healthy spacing-cross frequency for grid harvesting

Usage:
  python backtest/scan_m5_grid_symbols.py
  python backtest/scan_m5_grid_symbols.py --symbols EURCHF,EURUSD,GBPUSD --bars 4000
  python backtest/scan_m5_grid_symbols.py --top 15 --csv data/m5_grid_scan.csv
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5

from brokers.mt5 import MT5Broker
from config import settings as cfg
from grid.regime import TrendRegimeFilter, compute_trend_z
from grid.scanner import FX_UNIVERSE
from grid.spacing import VolEstimator, compute_spacing
from backtest.real_data_backtest import resolve_symbol


@dataclass
class GridM5Score:
    symbol: str
    score: float
    verdict: str
    range_quality: float
    anti_trend_quality: float
    edge_quality: float
    harvest_quality: float
    details: dict


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _parse_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _unique_keep_order(items: Iterable[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _resolve_symbols(base_symbols: list[str]) -> list[str]:
    resolved: list[str] = []
    for base in base_symbols:
        candidates = [resolve_symbol(base), base]
        for candidate in _unique_keep_order(candidates):
            info = mt5.symbol_info(candidate)
            if info is None:
                continue
            if not info.visible:
                mt5.symbol_select(candidate, True)
            rates = mt5.copy_rates_from_pos(candidate, mt5.TIMEFRAME_M5, 0, 20)
            if rates is None or len(rates) == 0:
                continue
            resolved.append(candidate)
            break
    return _unique_keep_order(resolved)


def _ema_center(prices: np.ndarray, halflife_seconds: int, bar_seconds: int = 300) -> np.ndarray:
    if len(prices) == 0:
        return prices
    alpha = 1.0 - math.exp(-math.log(2.0) * bar_seconds / max(1.0, float(halflife_seconds)))
    center = np.empty_like(prices)
    center[0] = prices[0]
    for i in range(1, len(prices)):
        center[i] = (1.0 - alpha) * center[i - 1] + alpha * prices[i]
    return center


def _analyze_symbol(broker: MT5Broker, symbol: str, bars: int) -> GridM5Score | None:
    if not broker.select_symbol(symbol):
        return None

    sym_info = broker.symbol_info(symbol)
    if sym_info is None:
        return None

    df = broker.get_rates(symbol, "M5", bars)
    if df is None or len(df) < 300:
        return None

    tick = broker.symbol_tick(symbol) or {}
    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", 0.0))

    point = float(sym_info.get("point", 0.00001))
    digits = int(sym_info.get("digits", 5) or 5)
    pip_size = point * 10.0 if digits in (3, 5) else point
    tick_size = float(sym_info.get("trade_tick_size") or point)
    if tick_size <= 0:
        return None

    close = df["close"].astype(float).values
    if np.any(close <= 0):
        return None

    live_spread = (ask - bid) if bid > 0 and ask > 0 else 0.0
    if "spread" in df.columns:
        spreads = df["spread"].astype(float).values * point
        spread_fallback = max(live_spread, tick_size)
        spread_series = np.where(spreads > 0, spreads, spread_fallback)
        median_spread = float(np.median(spread_series))
    else:
        spread_fallback = max(live_spread, tick_size)
        spread_series = np.full(len(close), spread_fallback, dtype=float)
        median_spread = float(spread_fallback)

    times = df["time"].astype("int64") // 10**9

    # Spacing/edge simulation with the SAME formula used by live grid engine
    bar_seconds = 300.0
    vol = VolEstimator(cfg.VOL_EWMA_LAMBDA, reference_dt=bar_seconds)
    spacings: list[float] = []
    cost_floors: list[float] = []
    edge_ok_count = 0
    edge_ticks_list: list[float] = []

    for i in range(len(close)):
        sigma = vol.update(float(close[i]), ts=float(times.iloc[i]))
        spread = float(spread_series[i])
        spacing, cost_floor = compute_spacing(
            mid=float(close[i]),
            spread=spread,
            tick_size=tick_size,
            sigma=sigma,
            step_seconds=bar_seconds,
            horizon_seconds=cfg.VOL_HORIZON_SECONDS,
            k_sigma=cfg.GRID_SPACING_K_SIGMA,
            k_cost=cfg.GRID_SPACING_K_COST,
            min_ticks=cfg.GRID_SPACING_MIN_TICKS,
            slip_ticks=cfg.SLIPPAGE_BUFFER_TICKS,
        )
        spacings.append(spacing)
        cost_floors.append(cost_floor)
        edge_ticks = (spacing - cost_floor) / max(tick_size, 1e-12)
        edge_ticks_list.append(edge_ticks)
        if edge_ticks >= cfg.EDGE_MIN_TICKS:
            edge_ok_count += 1

    spacing_arr = np.array(spacings)
    cost_arr = np.array(cost_floors)

    # Trend state using the SAME model currently used by live engine
    trend_filter = TrendRegimeFilter(
        trend_thresh=cfg.TREND_SLOPE_Z,
        trend_pause_mult=cfg.TREND_HARD_PAUSE_MULT,
        trend_confirm_bars=cfg.TREND_CONFIRM_BARS,
        range_confirm_bars=cfg.RANGE_CONFIRM_BARS,
    )

    trend_bars = 0
    max_trend_streak = 0
    current_streak = 0
    trend_thresh = cfg.TREND_SLOPE_Z
    hard_trend_thresh = cfg.TREND_SLOPE_Z * cfg.TREND_HARD_PAUSE_MULT

    for i in range(len(close)):
        if i < 120:
            continue
        window = close[max(0, i - 120): i + 1].tolist()
        if len(window) < 15:
            continue

        # Simple vol ratio proxy for detector context
        ret = np.diff(np.log(np.array(window)))
        if len(ret) < 12:
            vol_ratio = 1.0
        else:
            fast = float(np.std(ret[-12:]))
            slow = float(np.std(ret))
            vol_ratio = fast / max(slow, 1e-9)

        state = trend_filter.update(
            prices=window,
            vol_ratio=vol_ratio,
            spread_ratio=1.0,
            spread_thresh=cfg.SPREAD_PAUSE_MULT,
            vol_thresh=cfg.VOL_SHOCK_RATIO,
        )

        # Instantaneous trend evidence (independent from state machine)
        z_fast = abs(compute_trend_z(window[-24:]))
        z_mid = abs(compute_trend_z(window[-96:]))
        seg = np.array(window[-24:])
        logret = np.diff(np.log(seg)) if np.all(seg > 0) else np.array([])
        if len(logret) > 0:
            pos = np.sum(logret > 0)
            neg = np.sum(logret < 0)
            dir_persist = abs(pos - neg) / max(1, len(logret))
        else:
            dir_persist = 0.0

        instant_trend = (
            z_fast >= hard_trend_thresh
            or (z_fast >= trend_thresh and z_mid >= trend_thresh * 0.8 and dir_persist >= 0.45)
        )

        if state.regime == "TREND" or instant_trend:
            trend_bars += 1
            current_streak += 1
            max_trend_streak = max(max_trend_streak, current_streak)
        else:
            current_streak = 0

    valid_bars = max(1, len(close) - 30)
    trend_fraction = trend_bars / valid_bars
    range_fraction = 1.0 - trend_fraction

    # Harvest frequency: how often price crosses one spacing equivalent
    move = np.abs(np.diff(close))
    spacing_for_move = np.maximum(spacing_arr[:-1], tick_size)
    crossings = float(np.sum(move / spacing_for_move)) if len(move) > 0 else 0.0
    bars_per_day = 288.0
    crossings_per_day = crossings * (bars_per_day / max(len(move), 1))

    # Bounce quality: after moving >= spacing from center, does it revert quickly?
    center = _ema_center(close, cfg.ANCHOR_HALFLIFE_SECONDS, bar_seconds=300)
    lookahead = 6  # ~30 minutes on M5
    signals = 0
    bounces = 0
    for i in range(50, len(close) - lookahead):
        dist = abs(close[i] - center[i])
        if dist >= spacing_arr[i]:
            signals += 1
            segment = close[i + 1: i + 1 + lookahead]
            # reversion toward center by at least half a spacing
            if np.min(np.abs(segment - center[i])) <= 0.5 * spacing_arr[i]:
                bounces += 1
    bounce_rate = (bounces / signals) if signals > 0 else 0.0

    edge_ok_ratio = edge_ok_count / max(1, len(close))
    median_spacing = float(np.median(spacing_arr))
    median_spread = float(np.median(cost_arr))
    spacing_cost_ratio = median_spacing / max(median_spread, 1e-12)

    # Score components tuned for your objective: avoid persistent M5 trends
    range_quality = _clamp((range_fraction - 0.45) / 0.45)  # prefer >=90% range bars
    anti_trend_quality = _clamp(1.0 - max_trend_streak / max(24.0, 0.20 * len(close)))
    edge_quality = _clamp(0.65 * edge_ok_ratio + 0.35 * _clamp((spacing_cost_ratio - 1.2) / 2.0))

    # Sweet-spot crossings/day: too low => dead, too high => noisy trend/churn
    if crossings_per_day < 8:
        crossing_score = crossings_per_day / 8.0
    elif crossings_per_day <= 90:
        crossing_score = 1.0
    else:
        crossing_score = _clamp(1.0 - (crossings_per_day - 90.0) / 110.0)

    harvest_quality = _clamp(0.55 * crossing_score + 0.45 * bounce_rate)

    composite = (
        0.32 * range_quality
        + 0.24 * anti_trend_quality
        + 0.22 * edge_quality
        + 0.22 * harvest_quality
    ) * 100.0

    if composite >= 75:
        verdict = "EXCELLENT"
    elif composite >= 60:
        verdict = "GOOD"
    elif composite >= 45:
        verdict = "MARGINAL"
    else:
        verdict = "AVOID"

    return GridM5Score(
        symbol=symbol,
        score=round(composite, 1),
        verdict=verdict,
        range_quality=round(range_quality, 3),
        anti_trend_quality=round(anti_trend_quality, 3),
        edge_quality=round(edge_quality, 3),
        harvest_quality=round(harvest_quality, 3),
        details={
            "trend_fraction": round(trend_fraction, 3),
            "range_fraction": round(range_fraction, 3),
            "max_trend_streak_bars": int(max_trend_streak),
            "max_trend_streak_hours": round(max_trend_streak * 5 / 60.0, 2),
            "edge_ok_ratio": round(edge_ok_ratio, 3),
            "spacing_cost_ratio": round(spacing_cost_ratio, 3),
            "crossings_per_day": round(crossings_per_day, 1),
            "bounce_rate": round(bounce_rate, 3),
            "median_spacing_pips": round(median_spacing / pip_size, 2),
            "median_cost_floor_pips": round(median_spread / pip_size, 2),
        },
    )


def _print_results(results: list[GridM5Score], top_n: int):
    if top_n > 0:
        results = results[:top_n]

    print("\n" + "=" * 126)
    print("  M5 GRID-FRIENDLY SYMBOL RANKING (Trend-aware, Spacing-aware)")
    print("=" * 126)
    print(
        f"  {'#':>3} {'Symbol':<12} {'Score':>6} {'Verdict':<10} {'Range%':>7} {'Trend%':>7} "
        f"{'MaxTrend(h)':>11} {'Cross/day':>10} {'Bounce%':>8} {'Edge%':>7} "
        f"{'Spacing':>8} {'CostFloor':>9}"
    )
    print("  " + "-" * 121)

    for idx, r in enumerate(results, 1):
        d = r.details
        print(
            f"  {idx:>3} {r.symbol:<12} {r.score:>6.1f} {r.verdict:<10} "
            f"{d['range_fraction'] * 100:>6.1f}% {d['trend_fraction'] * 100:>6.1f}% "
            f"{d['max_trend_streak_hours']:>11.2f} {d['crossings_per_day']:>10.1f} "
            f"{d['bounce_rate'] * 100:>7.1f}% {d['edge_ok_ratio'] * 100:>6.1f}% "
            f"{d['median_spacing_pips']:>7.2f} {d['median_cost_floor_pips']:>8.2f}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Rank symbols by M5 grid-friendliness using live grid spacing + trend model"
    )
    parser.add_argument("--symbols", default="", help="Comma-separated symbols (default: FX universe)")
    parser.add_argument("--bars", type=int, default=3500, help="M5 bars per symbol (default: 3500)")
    parser.add_argument("--top", type=int, default=20, help="Show top N (default: 20; 0=all)")
    parser.add_argument("--csv", default="", help="Optional CSV output path")
    args = parser.parse_args()

    broker = MT5Broker()
    if not broker.connect():
        print("ERROR: cannot connect to MT5")
        return

    if args.symbols:
        base_symbols = _parse_symbols(args.symbols)
    else:
        base_symbols = FX_UNIVERSE

    symbols = _resolve_symbols(base_symbols)
    if not symbols:
        print("No symbols with M5 data found. Add symbols to MarketWatch and retry.")
        broker.disconnect()
        return

    print(f"Scanning {len(symbols)} symbols on M5 with {args.bars} bars each ...")

    scores: list[GridM5Score] = []
    for symbol in symbols:
        try:
            result = _analyze_symbol(broker, symbol, args.bars)
            if result is not None:
                scores.append(result)
        except Exception as exc:
            print(f"WARN: failed {symbol}: {exc}")

    broker.disconnect()

    if not scores:
        print("No results.")
        return

    scores.sort(key=lambda s: s.score, reverse=True)
    _print_results(scores, args.top)

    if args.csv:
        rows = []
        for s in scores:
            row = {
                "symbol": s.symbol,
                "score": s.score,
                "verdict": s.verdict,
                "range_quality": s.range_quality,
                "anti_trend_quality": s.anti_trend_quality,
                "edge_quality": s.edge_quality,
                "harvest_quality": s.harvest_quality,
            }
            row.update(s.details)
            rows.append(row)
        pd.DataFrame(rows).to_csv(args.csv, index=False)
        print(f"\nCSV saved to: {args.csv}")

    print("\nInterpretation:")
    print("- Prefer EXCELLENT/GOOD with high Range%, low Trend%, and short MaxTrend(h).")
    print("- Cross/day should be healthy but not extreme; Bounce% confirms choppiness.")
    print("- Edge% and Spacing vs CostFloor show if your configured grid has statistical room.")


if __name__ == "__main__":
    main()
