"""
The Inventory Recycling Trick
=============================

The insight: when a grid rung fills and price trends away, the EXIT order
sits far from current price, waiting forever. Meanwhile the rung is dead —
it can't participate in new oscillations.

Standard approaches (all flawed):
- Stop loss: locks in loss, horrible payoff ratio
- Time exit: same thing with a delay
- Tighter filter: helps marginally, entries already filled

THE TRICK: Don't wait for the original exit price. When price has moved
far enough away that the original exit is unlikely to fill soon, MOVE the
exit to the NEW grid center. Accept a partial loss on this rung, but
immediately recycle it into a fresh entry at the new center.

Why this works:
- Instead of holding a losing position hoping for a full reversal,
  we take a SMALL partial loss and immediately get back into the
  current oscillation zone
- The rung is recycled, not killed. It starts earning again immediately.
- The loss is typically 1-2 spacings, not 5-10 spacings
- We're converting dead inventory into live, productive inventory

The math:
- Old approach: hold for +1 spacing TP or -infinity (unrealized)
- New approach: hold for +1 spacing TP or -K spacing (recycled)
  where K is typically 1-2 spacings (the distance the center moved)

This is essentially a ROLLING GRID that never has dead weight.
Every rung is always working near the current center.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BT_COMMISSION_PER_LOT'] = '0'
os.environ['RUNG_STOP_LOSS_SPACINGS'] = '0'

import math
import numpy as np
import pandas as pd
from dataclasses import dataclass
from statistics import median

from config import settings as cfg
from grid.anchor import EmaAnchor
from grid.orders import build_rungs, update_rung_on_fill
from grid.sizing import size_for_rung
from grid.spacing import VolEstimator, compute_spacing
from grid.regime import classify_regime, compute_trend_z
from backtest.fills import can_fill_limit, apply_slippage
from backtest.grid_engine import BacktestResult, TradeRecord, _max_drawdown, _sharpe_ratio, _sortino_ratio, _cvar_95


class RecyclingGridBacktest:
    """Grid backtest with the inventory recycling trick.

    When the grid center moves and a rung's exit is now too far from
    the action, we close the rung at a partial loss and re-anchor it
    to the new center. The rung immediately becomes a fresh entry in
    the current oscillation zone.

    The key parameter is RECYCLE_THRESHOLD: how many spacings away
    from the current center does an exit need to be before we recycle it.
    """

    def __init__(self, df, contract_size, tick_size, bar_seconds=300.0,
                 recycle_threshold=2.0):
        self.df = df
        self.contract_size = contract_size
        self.tick_size = tick_size
        self.bar_seconds = bar_seconds
        self.recycle_threshold = recycle_threshold

        self.anchor = EmaAnchor(cfg.ANCHOR_HALFLIFE_SECONDS)
        self.vol_fast = VolEstimator(cfg.VOL_EWMA_LAMBDA, reference_dt=bar_seconds)
        self.vol_slow = VolEstimator(
            min(cfg.VOL_EWMA_LAMBDA + 0.02, 0.995), reference_dt=bar_seconds
        )
        self.rungs = []

    def run(self, starting_equity=10000.0, slip_ticks=1, spread_mult=1.0):
        equity_curve = []
        returns = []
        realized_pnl = 0.0
        trade_count = 0
        win_count = 0
        loss_count = 0
        gross_profit = 0.0
        gross_loss = 0.0
        max_inventory = 0.0
        recycle_count = 0
        recycle_loss = 0.0
        trade_records = []

        inventory_lots = 0.0
        center = 0.0
        spacing = 0.0
        price_history = []
        spread_history = []
        sim_ts = 0.0

        for _, row in self.df.iterrows():
            bar = row.to_dict()
            mid = float(bar["mid_close"])
            spread = float(bar["ask_close"] - bar["bid_close"]) * spread_mult
            sim_ts += self.bar_seconds

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
                mid=mid, spread=spread, tick_size=self.tick_size,
                sigma=sigma_fast, step_seconds=self.bar_seconds,
                horizon_seconds=cfg.VOL_HORIZON_SECONDS,
                k_sigma=cfg.GRID_SPACING_K_SIGMA, k_cost=cfg.GRID_SPACING_K_COST,
                min_ticks=cfg.GRID_SPACING_MIN_TICKS, slip_ticks=cfg.SLIPPAGE_BUFFER_TICKS,
            )
            if spacing_new <= 0:
                equity = starting_equity + realized_pnl
                equity_curve.append(equity)
                continue
            spacing = spacing_new

            # Regime
            allow_entries = True
            if len(price_history) >= 10:
                trend_z = compute_trend_z(price_history)
                spread_ratio = (
                    (spread / self.tick_size) / max(1.0, median(spread_history))
                    if len(spread_history) > 1 else 1.0
                )
                regime = classify_regime(
                    trend_z=trend_z, vol_ratio=vol_ratio,
                    spread_ratio=spread_ratio,
                    trend_thresh=cfg.TREND_SLOPE_Z,
                    vol_thresh=cfg.VOL_SHOCK_RATIO,
                    spread_thresh=cfg.SPREAD_PAUSE_MULT,
                )
                if regime.mode == "PAUSED":
                    allow_entries = False

            inv_ratio = 0.0
            if cfg.MAX_INVENTORY_LOTS > 0:
                inv_ratio = max(-1.0, min(1.0, inventory_lots / cfg.MAX_INVENTORY_LOTS))

            new_center = anchor - cfg.CENTER_SKEW_K * inv_ratio * spacing

            if center == 0.0:
                center = new_center
                self.rungs = build_rungs("BT", center, spacing, cfg.GRID_LEVELS)
            elif abs(new_center - center) >= cfg.GRID_RESET_K * spacing:
                old_center = center
                center = new_center

                # ══════════════════════════════════════════════════════════
                # THE TRICK: Recycle stuck EXIT rungs
                # ══════════════════════════════════════════════════════════
                for rung in self.rungs:
                    if rung.state == "EXIT" and rung.fill_price is not None:
                        # How far is this rung's exit from the new center?
                        distance_from_center = abs(
                            (rung.exit_price or 0) - center
                        ) / spacing

                        # If the exit is too far from where the action is now,
                        # close the position at current mid and recycle the rung
                        if distance_from_center > self.recycle_threshold:
                            # Realize the partial loss at current mid
                            if rung.entry_side == "BUY":
                                pnl = (mid - rung.fill_price) * rung.size * self.contract_size
                                inventory_lots -= rung.size
                            else:
                                pnl = (rung.fill_price - mid) * rung.size * self.contract_size
                                inventory_lots += rung.size

                            realized_pnl += pnl
                            trade_count += 1
                            if pnl > 0:
                                win_count += 1
                                gross_profit += pnl
                            else:
                                loss_count += 1
                                gross_loss += abs(pnl)
                            recycle_count += 1
                            if pnl < 0:
                                recycle_loss += abs(pnl)

                            trade_records.append(TradeRecord(
                                bar_index=len(equity_curve),
                                side=rung.entry_side,
                                entry_price=rung.fill_price,
                                exit_price=mid,
                                volume=rung.size,
                                pnl_gross=pnl,
                                pnl_net=pnl,
                                rung_id=rung.rung_id + "_R",
                            ))

                            # Reset rung to ENTRY at the new center
                            rung.state = "ENTRY"
                            rung.fill_price = None
                            rung.exit_price = None
                            rung.entry_price = center + rung.level_index * spacing

                # Update ENTRY rungs to new center
                for rung in self.rungs:
                    if rung.state == "ENTRY":
                        rung.entry_price = center + rung.level_index * spacing

            # Edge check
            edge_ok = spacing - cost_floor >= cfg.EDGE_MIN_TICKS * self.tick_size
            can_enter = allow_entries and edge_ok

            # Check fills
            for rung in self.rungs:
                if rung.state == "ENTRY":
                    if not can_enter:
                        continue
                    side = rung.entry_side
                    price = rung.entry_price

                    if inv_ratio >= 0.95 and side == "BUY":
                        continue
                    if inv_ratio <= -0.95 and side == "SELL":
                        continue

                    if can_fill_limit(side, price, bar):
                        fill_price = apply_slippage(side, price, self.tick_size, slip_ticks)
                        rung.size = size_for_rung(
                            side=side, level_index=rung.level_index,
                            base_size=cfg.BASE_ORDER_SIZE_LOTS,
                            inv_ratio=inv_ratio,
                            gamma=cfg.INVENTORY_SKEW_GAMMA,
                            eta=cfg.SIZE_TAPER_ETA,
                        )
                        if rung.size <= 0:
                            continue

                        if side == "BUY":
                            inventory_lots += rung.size
                        else:
                            inventory_lots -= rung.size
                        max_inventory = max(max_inventory, abs(inventory_lots))
                        update_rung_on_fill(rung, fill_price, spacing, "")

                elif rung.state == "EXIT":
                    side = "SELL" if rung.entry_side == "BUY" else "BUY"
                    price = rung.exit_price or 0.0
                    if price > 0 and can_fill_limit(side, price, bar):
                        fill_price = apply_slippage(side, price, self.tick_size, slip_ticks)
                        if rung.entry_side == "BUY":
                            pnl = (fill_price - (rung.fill_price or 0)) * rung.size * self.contract_size
                        else:
                            pnl = ((rung.fill_price or 0) - fill_price) * rung.size * self.contract_size

                        realized_pnl += pnl
                        trade_count += 1
                        if pnl > 0:
                            win_count += 1
                            gross_profit += pnl
                        else:
                            loss_count += 1
                            gross_loss += abs(pnl)

                        trade_records.append(TradeRecord(
                            bar_index=len(equity_curve), side=rung.entry_side,
                            entry_price=rung.fill_price or 0, exit_price=fill_price,
                            volume=rung.size, pnl_gross=pnl, pnl_net=pnl,
                            rung_id=rung.rung_id,
                        ))

                        if side == "BUY":
                            inventory_lots += rung.size
                        else:
                            inventory_lots -= rung.size

                        update_rung_on_fill(rung, fill_price, spacing, "")
                        rung.entry_price = center + rung.level_index * spacing

            # Unrealized
            unrealized = 0.0
            for rung in self.rungs:
                if rung.state == "EXIT" and rung.fill_price is not None:
                    if rung.entry_side == "BUY":
                        unrealized += (mid - rung.fill_price) * rung.size * self.contract_size
                    else:
                        unrealized += (rung.fill_price - mid) * rung.size * self.contract_size

            equity = starting_equity + realized_pnl + unrealized
            equity_curve.append(equity)
            if len(equity_curve) >= 2 and equity_curve[-2] > 0:
                returns.append((equity_curve[-1] - equity_curve[-2]) / equity_curve[-2])

        max_dd = _max_drawdown(equity_curve)
        total_net = (equity_curve[-1] - starting_equity) if equity_curve else 0.0
        sharpe = _sharpe_ratio(returns, self.bar_seconds)
        sortino = _sortino_ratio(returns, self.bar_seconds)
        pf = gross_profit / max(gross_loss, 1e-9)
        calmar = (total_net / starting_equity * 100) / max(max_dd, 0.01)

        return BacktestResult(
            equity_curve=equity_curve,
            total_pnl=realized_pnl,
            total_pnl_net=total_net,
            max_drawdown=max_dd,
            trades=trade_count,
            wins=win_count,
            losses=loss_count,
            total_commission=0.0,
            total_swap=0.0,
            sharpe=sharpe,
            sortino=sortino,
            profit_factor=pf,
            calmar=calmar,
            cvar_95=_cvar_95(returns),
            max_inventory=max_inventory,
            trade_records=trade_records,
        ), recycle_count, recycle_loss


# ═══════════════════════════════════════════════════════════════════
#  TEST: Compare Original vs Recycling on REAL OANDA DATA
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import MetaTrader5 as mt5
    from brokers.mt5 import MT5Broker, TF_MAP
    from backtest.grid_engine import GridBacktestEngine
    from backtest.real_data_backtest import download_data, OANDA_SYMBOL_MAP, resolve_symbol

    broker = MT5Broker()
    if not broker.connect():
        print("Cannot connect to MT5")
        sys.exit(1)

    # Test on real data for multiple symbols
    test_symbols = ["EURUSD", "EURCAD", "EURCHF", "NZDUSD", "EURGBP",
                    "GBPUSD", "AUDNZD", "GBPCHF", "NZDCAD", "EURAUD"]

    print()
    print("=" * 90)
    print("  INVENTORY RECYCLING TRICK — Real Data Comparison")
    print("  Original Grid vs Recycling Grid (recycle_threshold=2.0 spacings)")
    print("=" * 90)

    print(f"\n  {'Symbol':<14} {'--- ORIGINAL ---':>30}   {'--- RECYCLED ---':>30}   {'Recycled':>8} {'Loss$':>7}")
    print(f"  {'':14} {'PnL':>10} {'Trades':>7} {'DD':>6}   {'PnL':>10} {'Trades':>7} {'DD':>6}   {'Count':>8} {'Saved':>7}")
    print("  " + "-" * 88)

    orig_total = 0
    recy_total = 0

    for base in test_symbols:
        real = resolve_symbol(base)
        mt5.symbol_select(real, True)
        df, params = download_data(broker, real, timeframe="M5", bars=20000)
        if df is None:
            print(f"  {real:<14} NO DATA")
            continue

        # Original backtest
        engine_orig = GridBacktestEngine(
            df, params["contract_size"], params["tick_size"],
            commission_per_lot=0.0, bar_seconds=300.0,
        )
        r_orig = engine_orig.run(starting_equity=10000.0, slip_ticks=1)

        # Recycling backtest
        engine_recy = RecyclingGridBacktest(
            df, params["contract_size"], params["tick_size"],
            bar_seconds=300.0, recycle_threshold=2.0,
        )
        r_recy, recy_count, recy_loss = engine_recy.run(starting_equity=10000.0, slip_ticks=1)

        orig_total += r_orig.total_pnl_net
        recy_total += r_recy.total_pnl_net

        diff = r_recy.total_pnl_net - r_orig.total_pnl_net
        marker = ">>>" if diff > 1 else "   "

        print(f"  {real:<14} ${r_orig.total_pnl_net:>9.2f} {r_orig.trades:>7} {r_orig.max_drawdown:>5.1f}%"
              f"   ${r_recy.total_pnl_net:>9.2f} {r_recy.trades:>7} {r_recy.max_drawdown:>5.1f}%"
              f"   {recy_count:>8} ${recy_loss:>6.2f} {marker}")

    print("  " + "-" * 88)
    print(f"  {'TOTAL':<14} ${orig_total:>9.2f} {'':>7} {'':>6}"
          f"   ${recy_total:>9.2f}")
    diff_total = recy_total - orig_total
    print(f"\n  Recycling advantage: ${diff_total:>+.2f}")
    if diff_total > 0:
        print(f"  Recycling WINS by ${diff_total:.2f}")
    else:
        print(f"  Original WINS by ${-diff_total:.2f}")

    broker.disconnect()
