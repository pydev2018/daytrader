"""
Grid backtest engine using bid/ask bars.

Fixes:
- S1 #8: Full inventory tracking with inv_ratio fed to sizing (was hardcoded 0.0).
- S2 #14: Commission and swap costs modeled.
- Added: Comprehensive metrics (Sharpe, Sortino, profit factor, CVaR).
- Added: Walk-forward validation support.
- Added: Configurable stress-test parameters (spread mult, slippage, gaps).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from config import settings as cfg
from grid.anchor import EmaAnchor
from grid.orders import build_rungs, update_rung_on_fill
from grid.regime import classify_regime, compute_trend_z
from grid.sizing import size_for_rung
from grid.spacing import VolEstimator, compute_spacing
from backtest.fills import can_fill_limit, apply_slippage


# ═════════════════════════════════════════════════════════════════════════════
#  RESULT MODELS
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    bar_index: int
    side: str
    entry_price: float
    exit_price: float
    volume: float
    pnl_gross: float
    pnl_net: float  # after commission
    rung_id: str


@dataclass
class BacktestResult:
    equity_curve: list[float]
    total_pnl: float
    total_pnl_net: float  # after all costs
    max_drawdown: float
    trades: int
    wins: int
    losses: int
    total_commission: float
    total_swap: float
    sharpe: float
    sortino: float
    profit_factor: float
    calmar: float
    cvar_95: float
    max_inventory: float
    trade_records: list[TradeRecord] = field(default_factory=list)


# ═════════════════════════════════════════════════════════════════════════════
#  BACKTEST ENGINE
# ═════════════════════════════════════════════════════════════════════════════

class GridBacktestEngine:
    """Event-driven grid backtest with realistic cost modeling.

    Features:
    - Tracks net inventory and feeds inv_ratio to sizing.
    - Models commissions (per lot, round-trip) and swap (per lot per day).
    - Regime filter pauses during trends/vol shocks.
    - Weekend wind-down: closes stuck positions Friday, blocks entries over weekend.
    - vol_min skip: orders below broker minimum are dropped, not clamped up.
    - Records individual trades for post-hoc analysis.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        contract_size: float,
        tick_size: float,
        commission_per_lot: float | None = None,
        swap_per_lot_per_day: float | None = None,
        bar_seconds: float = 60.0,
        weekend_close_threshold: float | None = None,
        vol_min: float = 0.01,
    ):
        self.df = df
        self.contract_size = contract_size
        self.tick_size = tick_size
        self.commission_per_lot = (
            commission_per_lot if commission_per_lot is not None
            else cfg.BT_COMMISSION_PER_LOT
        )
        self.swap_per_lot_per_day = (
            swap_per_lot_per_day if swap_per_lot_per_day is not None
            else cfg.BT_SWAP_PER_LOT_PER_DAY
        )
        self.bar_seconds = bar_seconds
        self.weekend_close_threshold = (
            weekend_close_threshold if weekend_close_threshold is not None
            else cfg.WEEKEND_CLOSE_THRESHOLD_SPACINGS
        )
        self.vol_min = vol_min
        self.anchor = EmaAnchor(cfg.ANCHOR_HALFLIFE_SECONDS)
        self.vol_fast = VolEstimator(cfg.VOL_EWMA_LAMBDA, reference_dt=bar_seconds)
        self.vol_slow = VolEstimator(
            min(cfg.VOL_EWMA_LAMBDA + 0.02, 0.995), reference_dt=bar_seconds
        )
        self.rungs = []

    def run(
        self,
        starting_equity: float = 10_000.0,
        slip_ticks: int | None = None,
        spread_mult: float = 1.0,
        use_regime_filter: bool = True,
    ) -> BacktestResult:
        if slip_ticks is None:
            slip_ticks = cfg.BT_DEFAULT_SLIPPAGE_TICKS

        equity_curve: list[float] = []
        returns: list[float] = []
        realized_pnl = 0.0
        total_commission = 0.0
        total_swap = 0.0
        trade_count = 0
        win_count = 0
        loss_count = 0
        gross_profit = 0.0
        gross_loss = 0.0
        max_inventory = 0.0
        trade_records: list[TradeRecord] = []

        # Inventory tracking
        inventory_lots = 0.0
        center = 0.0
        spacing = 0.0
        price_history: list[float] = []
        spread_history: list[float] = []
        bar_count = 0
        sim_ts = 0.0  # Simulated timestamp for time-aware estimators

        weekend_closed = False  # track if we already did wind-down this weekend
        weekend_close_count = 0
        weekend_close_pnl = 0.0

        for idx, row in self.df.iterrows():
            bar = row.to_dict()
            mid = float(bar["mid_close"])
            raw_spread = float(bar["ask_close"] - bar["bid_close"])
            spread = raw_spread * spread_mult
            bar_count += 1
            sim_ts += self.bar_seconds  # Advance simulated clock

            # ── Weekend detection and wind-down ──────────────────────────
            # If the dataframe has a 'time' column, use it for real weekend detection
            bar_time = bar.get("time", None)
            is_friday_close = False
            is_weekend = False
            if bar_time is not None and hasattr(bar_time, 'weekday'):
                weekday = bar_time.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
                hour = bar_time.hour if hasattr(bar_time, 'hour') else 0
                is_friday_close = (weekday == 4 and hour >= 21)
                is_weekend = weekday >= 5

                if is_friday_close and not weekend_closed and self.weekend_close_threshold > 0:
                    # Pre-weekend wind-down: close stuck EXIT positions
                    for rung in self.rungs:
                        if rung.state != "EXIT" or rung.exit_price is None:
                            continue
                        if rung.size <= 0:
                            continue
                        distance = abs(rung.exit_price - mid) / max(spacing, self.tick_size)
                        if distance > self.weekend_close_threshold:
                            # Close at market (mid price with slippage)
                            close_side = "SELL" if rung.entry_side == "BUY" else "BUY"
                            close_price = apply_slippage(close_side, mid, self.tick_size, slip_ticks)
                            if rung.entry_side == "BUY":
                                pnl = (close_price - (rung.fill_price or 0)) * rung.size * self.contract_size
                                inventory_lots -= rung.size
                            else:
                                pnl = ((rung.fill_price or 0) - close_price) * rung.size * self.contract_size
                                inventory_lots += rung.size

                            realized_pnl += pnl
                            trade_count += 1
                            if pnl > 0:
                                win_count += 1
                                gross_profit += pnl
                            else:
                                loss_count += 1
                                gross_loss += abs(pnl)
                            weekend_close_count += 1
                            weekend_close_pnl += pnl

                            trade_records.append(TradeRecord(
                                bar_index=bar_count, side=rung.entry_side,
                                entry_price=rung.fill_price or 0, exit_price=close_price,
                                volume=rung.size, pnl_gross=pnl, pnl_net=pnl,
                                rung_id=rung.rung_id + "_WC",
                            ))
                            # Reset rung
                            rung.state = "ENTRY"
                            rung.fill_price = None
                            rung.exit_price = None
                            rung.entry_price = center + rung.level_index * spacing
                    weekend_closed = True

                if is_weekend:
                    # Skip trading during weekend — just compute equity
                    unrealized = 0.0
                    for rung in self.rungs:
                        if rung.state == "EXIT" and rung.fill_price is not None:
                            if rung.entry_side == "BUY":
                                unrealized += (mid - rung.fill_price) * rung.size * self.contract_size
                            else:
                                unrealized += (rung.fill_price - mid) * rung.size * self.contract_size
                    equity = starting_equity + realized_pnl + unrealized - total_commission - total_swap
                    equity_curve.append(equity)
                    continue

                # Reset weekend flag on Monday
                if weekday == 0:
                    weekend_closed = False

            # Track prices for regime
            price_history.append(mid)
            spread_history.append(spread / self.tick_size if self.tick_size > 0 else 0)
            if len(price_history) > 250:
                price_history.pop(0)
            if len(spread_history) > 250:
                spread_history.pop(0)

            anchor = self.anchor.update(mid, sim_ts)
            sigma_fast = self.vol_fast.update(mid, sim_ts)
            sigma_slow = self.vol_slow.update(mid, sim_ts)
            vol_ratio = sigma_fast / max(sigma_slow, 1e-9)

            spacing_new, cost_floor = compute_spacing(
                mid=mid,
                spread=spread,
                tick_size=self.tick_size,
                sigma=sigma_fast,
                step_seconds=self.bar_seconds,
                horizon_seconds=cfg.VOL_HORIZON_SECONDS,
                k_sigma=cfg.GRID_SPACING_K_SIGMA,
                k_cost=cfg.GRID_SPACING_K_COST,
                min_ticks=cfg.GRID_SPACING_MIN_TICKS,
                slip_ticks=cfg.SLIPPAGE_BUFFER_TICKS,
            )
            if spacing_new <= 0:
                equity = starting_equity + realized_pnl - total_commission - total_swap
                equity_curve.append(equity)
                continue
            spacing = spacing_new

            # Regime check
            allow_entries = True
            if use_regime_filter and len(price_history) >= 10:
                trend_z = compute_trend_z(price_history)
                from statistics import median as _median
                spread_ratio = (
                    (spread / self.tick_size) / max(1.0, _median(spread_history))
                    if len(spread_history) > 1
                    else 1.0
                )
                regime = classify_regime(
                    trend_z=trend_z,
                    vol_ratio=vol_ratio,
                    spread_ratio=spread_ratio,
                    trend_thresh=cfg.TREND_SLOPE_Z,
                    vol_thresh=cfg.VOL_SHOCK_RATIO,
                    spread_thresh=cfg.SPREAD_PAUSE_MULT,
                )
                if regime.mode == "PAUSED":
                    allow_entries = False

            # Inventory ratio
            inv_ratio = 0.0
            if cfg.MAX_INVENTORY_LOTS > 0:
                inv_ratio = max(
                    -1.0, min(1.0, inventory_lots / cfg.MAX_INVENTORY_LOTS)
                )

            # Center with inventory skew
            new_center = anchor - cfg.CENTER_SKEW_K * inv_ratio * spacing

            if center == 0.0:
                center = new_center
                self.rungs = build_rungs("BT", center, spacing, cfg.GRID_LEVELS)
            elif abs(new_center - center) >= cfg.GRID_RESET_K * spacing:
                center = new_center
                for rung in self.rungs:
                    if rung.state == "ENTRY":
                        rung.entry_price = center + rung.level_index * spacing

            # Edge check
            edge_ok = spacing - cost_floor >= cfg.EDGE_MIN_TICKS * self.tick_size
            can_enter = allow_entries and edge_ok and not is_friday_close

            # Check fills
            for rung in self.rungs:
                if rung.state == "ENTRY":
                    if not can_enter:
                        continue
                    side = rung.entry_side
                    price = rung.entry_price

                    # Inventory clamp
                    if inv_ratio >= 0.95 and side == "BUY":
                        continue
                    if inv_ratio <= -0.95 and side == "SELL":
                        continue

                    if can_fill_limit(side, price, bar):
                        fill_price = apply_slippage(
                            side, price, self.tick_size, slip_ticks
                        )
                        # Size with inventory tracking
                        rung.size = size_for_rung(
                            side=side,
                            level_index=rung.level_index,
                            base_size=cfg.BASE_ORDER_SIZE_LOTS,
                            inv_ratio=inv_ratio,
                            gamma=cfg.INVENTORY_SKEW_GAMMA,
                            eta=cfg.SIZE_TAPER_ETA,
                        )
                        # C1: Skip if below broker minimum (don't clamp up)
                        if rung.size < self.vol_min:
                            continue

                        # Commission on entry
                        commission = self.commission_per_lot * rung.size
                        total_commission += commission

                        # Update inventory
                        if side == "BUY":
                            inventory_lots += rung.size
                        else:
                            inventory_lots -= rung.size
                        max_inventory = max(max_inventory, abs(inventory_lots))

                        update_rung_on_fill(rung, fill_price, spacing, "")

                else:  # EXIT — check take-profit OR stop-loss
                    side = "SELL" if rung.entry_side == "BUY" else "BUY"
                    price = rung.exit_price or 0.0
                    fill_price = None
                    is_stop = False

                    # Check take-profit fill
                    if price > 0 and can_fill_limit(side, price, bar):
                        fill_price = apply_slippage(
                            side, price, self.tick_size, slip_ticks
                        )

                    # Check stop-loss: if price moved too far against us
                    elif cfg.RUNG_STOP_LOSS_SPACINGS > 0 and rung.fill_price:
                        if rung.entry_side == "BUY":
                            # Bought — stop if price fell N spacings below entry
                            stop_price = rung.fill_price - cfg.RUNG_STOP_LOSS_SPACINGS * spacing
                            if bar["bid_low"] <= stop_price:
                                fill_price = apply_slippage(
                                    "SELL", stop_price, self.tick_size, slip_ticks
                                )
                                is_stop = True
                        else:
                            # Sold — stop if price rose N spacings above entry
                            stop_price = rung.fill_price + cfg.RUNG_STOP_LOSS_SPACINGS * spacing
                            if bar["ask_high"] >= stop_price:
                                fill_price = apply_slippage(
                                    "BUY", stop_price, self.tick_size, slip_ticks
                                )
                                is_stop = True

                    if fill_price is not None:
                        # Realize PnL
                        if rung.entry_side == "BUY":
                            pnl = (
                                (fill_price - (rung.fill_price or 0.0))
                                * rung.size
                                * self.contract_size
                            )
                        else:
                            pnl = (
                                ((rung.fill_price or 0.0) - fill_price)
                                * rung.size
                                * self.contract_size
                            )

                        # Commission on exit
                        commission = self.commission_per_lot * rung.size
                        total_commission += commission

                        pnl_net = pnl - commission * 2  # round-trip
                        realized_pnl += pnl
                        trade_count += 1
                        if pnl_net > 0:
                            win_count += 1
                            gross_profit += pnl_net
                        else:
                            loss_count += 1
                            gross_loss += abs(pnl_net)

                        trade_records.append(TradeRecord(
                            bar_index=bar_count,
                            side=rung.entry_side,
                            entry_price=rung.fill_price or 0.0,
                            exit_price=fill_price,
                            volume=rung.size,
                            pnl_gross=pnl,
                            pnl_net=pnl_net,
                            rung_id=rung.rung_id,
                        ))

                        # Update inventory
                        if side == "BUY":
                            inventory_lots += rung.size
                        else:
                            inventory_lots -= rung.size

                        update_rung_on_fill(rung, fill_price, spacing, "")
                        rung.entry_price = center + rung.level_index * spacing

            # Swap cost (approximate: per bar, scaled by bar duration)
            if abs(inventory_lots) > 0 and self.swap_per_lot_per_day > 0:
                daily_fraction = self.bar_seconds / 86400.0
                swap_cost = (
                    abs(inventory_lots)
                    * self.swap_per_lot_per_day
                    * daily_fraction
                )
                total_swap += swap_cost

            # Unrealized PnL from open cycles
            unrealized = 0.0
            for rung in self.rungs:
                if rung.state == "EXIT" and rung.fill_price is not None:
                    if rung.entry_side == "BUY":
                        unrealized += (
                            (mid - rung.fill_price)
                            * rung.size
                            * self.contract_size
                        )
                    else:
                        unrealized += (
                            (rung.fill_price - mid)
                            * rung.size
                            * self.contract_size
                        )

            equity = (
                starting_equity
                + realized_pnl
                + unrealized
                - total_commission
                - total_swap
            )
            equity_curve.append(equity)

            # Period return for Sharpe calc
            if len(equity_curve) >= 2 and equity_curve[-2] > 0:
                ret = (equity_curve[-1] - equity_curve[-2]) / equity_curve[-2]
                returns.append(ret)

        # Compute metrics
        max_dd = _max_drawdown(equity_curve)
        total_net = (
            (equity_curve[-1] - starting_equity) if equity_curve else 0.0
        )
        sharpe = _sharpe_ratio(returns, self.bar_seconds)
        sortino = _sortino_ratio(returns, self.bar_seconds)
        pf = gross_profit / max(gross_loss, 1e-9)
        calmar = (total_net / starting_equity * 100) / max(max_dd, 0.01)
        cvar = _cvar_95(returns)

        return BacktestResult(
            equity_curve=equity_curve,
            total_pnl=realized_pnl,
            total_pnl_net=total_net,
            max_drawdown=max_dd,
            trades=trade_count,
            wins=win_count,
            losses=loss_count,
            total_commission=total_commission,
            total_swap=total_swap,
            sharpe=sharpe,
            sortino=sortino,
            profit_factor=pf,
            calmar=calmar,
            cvar_95=cvar,
            max_inventory=max_inventory,
            trade_records=trade_records,
        )


# ═════════════════════════════════════════════════════════════════════════════
#  WALK-FORWARD VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class WalkForwardWindow:
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_result: Optional[BacktestResult] = None
    test_result: Optional[BacktestResult] = None


def walk_forward(
    df: pd.DataFrame,
    contract_size: float,
    tick_size: float,
    n_windows: int = 5,
    train_ratio: float = 0.7,
    starting_equity: float = 10_000.0,
    slip_ticks: int | None = None,
    spread_mult: float = 1.0,
    bar_seconds: float = 60.0,
) -> list[WalkForwardWindow]:
    """Run walk-forward validation across rolling windows.

    Splits data into n_windows of roughly equal size, with overlapping
    train/test splits. Each window trains on train_ratio of its data
    and tests on the remainder.

    Returns per-window results for analysis of out-of-sample stability.
    """
    total_bars = len(df)
    window_size = total_bars // n_windows
    if window_size < 100:
        raise ValueError(f"Not enough data: {total_bars} bars / {n_windows} windows = {window_size}")

    results: list[WalkForwardWindow] = []

    for i in range(n_windows):
        start = i * window_size
        end = min(start + window_size * 2, total_bars)  # Overlap windows
        if end - start < 50:
            break

        split = start + int((end - start) * train_ratio)
        window = WalkForwardWindow(
            train_start=start,
            train_end=split,
            test_start=split,
            test_end=end,
        )

        # Train window
        train_df = df.iloc[start:split].reset_index(drop=True)
        if len(train_df) > 10:
            engine = GridBacktestEngine(
                train_df, contract_size, tick_size, bar_seconds=bar_seconds
            )
            window.train_result = engine.run(
                starting_equity=starting_equity,
                slip_ticks=slip_ticks,
                spread_mult=spread_mult,
            )

        # Test window (out-of-sample)
        test_df = df.iloc[split:end].reset_index(drop=True)
        if len(test_df) > 10:
            engine = GridBacktestEngine(
                test_df, contract_size, tick_size, bar_seconds=bar_seconds
            )
            window.test_result = engine.run(
                starting_equity=starting_equity,
                slip_ticks=slip_ticks,
                spread_mult=spread_mult,
            )

        results.append(window)

    return results


# ═════════════════════════════════════════════════════════════════════════════
#  METRICS HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd * 100


def _sharpe_ratio(
    returns: list[float], bar_seconds: float, risk_free: float = 0.0
) -> float:
    """Annualized Sharpe ratio."""
    if len(returns) < 2:
        return 0.0
    bars_per_year = 365.25 * 86400 / max(bar_seconds, 1.0)
    mean_r = sum(returns) / len(returns) - risk_free / bars_per_year
    std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1))
    if std_r == 0:
        return 0.0
    return (mean_r / std_r) * math.sqrt(bars_per_year)


def _sortino_ratio(
    returns: list[float], bar_seconds: float, risk_free: float = 0.0
) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    if len(returns) < 2:
        return 0.0
    bars_per_year = 365.25 * 86400 / max(bar_seconds, 1.0)
    mean_r = sum(returns) / len(returns) - risk_free / bars_per_year
    downside = [r for r in returns if r < 0]
    if not downside:
        return float("inf") if mean_r > 0 else 0.0
    down_std = math.sqrt(sum(r * r for r in downside) / len(downside))
    if down_std == 0:
        return 0.0
    return (mean_r / down_std) * math.sqrt(bars_per_year)


def _cvar_95(returns: list[float]) -> float:
    """Conditional Value at Risk at 95% confidence (expected shortfall)."""
    if len(returns) < 20:
        return 0.0
    sorted_r = sorted(returns)
    cutoff = max(1, int(len(sorted_r) * 0.05))
    tail = sorted_r[:cutoff]
    return abs(sum(tail) / len(tail)) * 100  # as percentage
