"""Diagnose why the grid strategy is underperforming."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from backtest.run_backtest import generate_synthetic
from backtest.grid_engine import GridBacktestEngine
from config import settings as cfg
from grid.spacing import VolEstimator, compute_spacing
from grid.anchor import EmaAnchor

df, params = generate_synthetic("EURUSD", 10000, 60.0, "mixed", 42)
spreads = (df["ask_close"] - df["bid_close"]).values
mids = df["mid_close"].values
tick_size = params["tick_size"]

print("=== MARKET CONDITIONS ===")
print(f"Avg spread: {np.mean(spreads):.6f} ({np.mean(spreads)/tick_size:.1f} ticks)")
print(f"Avg mid: {np.mean(mids):.5f}")
print(f"Price range: {np.min(mids):.5f} to {np.max(mids):.5f}")

vol = VolEstimator(cfg.VOL_EWMA_LAMBDA, reference_dt=60.0)
anchor = EmaAnchor(cfg.ANCHOR_HALFLIFE_SECONDS)
spacings = []
cost_floors = []

for i in range(len(mids)):
    mid = mids[i]
    spread = spreads[i]
    anchor.update(mid, float(i * 60))
    sigma = vol.update(mid, float(i * 60))
    if sigma > 0 and i > 50:
        sp, cf = compute_spacing(
            mid=mid, spread=spread, tick_size=tick_size, sigma=sigma,
            step_seconds=60.0, horizon_seconds=cfg.VOL_HORIZON_SECONDS,
            k_sigma=cfg.GRID_SPACING_K_SIGMA, k_cost=cfg.GRID_SPACING_K_COST,
            min_ticks=cfg.GRID_SPACING_MIN_TICKS, slip_ticks=cfg.SLIPPAGE_BUFFER_TICKS,
        )
        spacings.append(sp)
        cost_floors.append(cf)

spacings = np.array(spacings)
cost_floors = np.array(cost_floors)
edges = spacings - cost_floors

print(f"\n=== SPACING vs COST (THE PROBLEM) ===")
print(f"Avg spacing:    {np.mean(spacings):.6f} ({np.mean(spacings)/tick_size:.1f} ticks)")
print(f"Avg cost floor: {np.mean(cost_floors):.6f} ({np.mean(cost_floors)/tick_size:.1f} ticks)")
print(f"Avg edge:       {np.mean(edges):.6f} ({np.mean(edges)/tick_size:.1f} ticks)")
print(f"Edge / spacing: {np.mean(edges/spacings)*100:.1f}%")

profit_per_rt = np.mean(spacings) * 0.01 * params["contract_size"]
spread_cost = np.mean(cost_floors) * 0.01 * params["contract_size"]
commission_rt = cfg.BT_COMMISSION_PER_LOT * 0.01 * 2
total_cost = spread_cost + commission_rt

print(f"\n=== PER ROUND-TRIP (0.01 lots) ===")
print(f"Gross profit:  ${profit_per_rt:.4f}")
print(f"Spread+slip:   ${spread_cost:.4f}")
print(f"Commission RT: ${commission_rt:.4f}")
print(f"Total cost:    ${total_cost:.4f}")
print(f"NET per trade: ${profit_per_rt - total_cost:.4f}")
print(f"Break-even win rate needed: {total_cost/profit_per_rt*100:.1f}%")

print(f"\n=== CURRENT CONFIG VALUES ===")
print(f"k_sigma:      {cfg.GRID_SPACING_K_SIGMA}")
print(f"k_cost:       {cfg.GRID_SPACING_K_COST}")
print(f"Commission:   ${cfg.BT_COMMISSION_PER_LOT}/lot")
print(f"Slippage:     {cfg.BT_DEFAULT_SLIPPAGE_TICKS} ticks")
print(f"EDGE_MIN:     {cfg.EDGE_MIN_TICKS} ticks")
print(f"VOL_HORIZON:  {cfg.VOL_HORIZON_SECONDS}s")
print(f"ANCHOR_HL:    {cfg.ANCHOR_HALFLIFE_SECONDS}s")

# What happens during trending periods?
print(f"\n=== REGIME BREAKDOWN ===")
from grid.regime import compute_trend_z
window = 100
trend_bars = 0
range_bars = 0
for i in range(window, len(mids)):
    z = compute_trend_z(mids[i-window:i].tolist())
    if abs(z) > cfg.TREND_SLOPE_Z:
        trend_bars += 1
    else:
        range_bars += 1
print(f"Ranging bars: {range_bars} ({range_bars/(range_bars+trend_bars)*100:.0f}%)")
print(f"Trending bars: {trend_bars} ({trend_bars/(range_bars+trend_bars)*100:.0f}%)")

# The real question: what's the P&L in ranging vs trending?
print(f"\n=== THE REAL ISSUE ===")
print(f"In 'mixed' regime, the strategy PAUSES during trends (good).")
print(f"But when it's ranging, the spacing is so wide that each")
print(f"round-trip barely covers costs.")
print(f"")
print(f"The edge per trade is ${profit_per_rt - total_cost:.4f}")
print(f"With {204} trades over {10000} bars (~7 days), that's:")
print(f"  If all were winners: 204 * ${profit_per_rt - total_cost:.4f} = ${204*(profit_per_rt-total_cost):.2f}")
print(f"")
print(f"DIAGNOSIS: Commission of ${cfg.BT_COMMISSION_PER_LOT}/lot is too high")
print(f"for 0.01 lot grid. At 0.01 lots, ${commission_rt:.4f} per RT is")
print(f"{commission_rt/profit_per_rt*100:.0f}% of gross profit.")
print(f"")
print(f"OANDA MT5 typically has NO commission (it's in the spread).")
print(f"Setting BT_COMMISSION_PER_LOT=0 would be accurate for OANDA.")
