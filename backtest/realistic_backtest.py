"""
Realistic backtest: EURCAD on real OANDA data with ALL production features.
- Weekend wind-down (close stuck positions Friday)
- vol_min skip (no over-sizing)
- Regime filter
- Real spreads from MT5
- 0.08 lots, $1,000 capital

Compares: with vs without weekend wind-down to show exact cost.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BT_COMMISSION_PER_LOT'] = '0'
os.environ['RUNG_STOP_LOSS_SPACINGS'] = '0'
os.environ['BASE_ORDER_SIZE_LOTS'] = '0.08'
os.environ['GRID_LEVELS'] = '6'
os.environ['WEEKEND_CLOSE_THRESHOLD_SPACINGS'] = '2.0'

import importlib
import config.settings
importlib.reload(config.settings)

import MetaTrader5 as mt5
from brokers.mt5 import MT5Broker
from backtest.real_data_backtest import download_data
from backtest.grid_engine import GridBacktestEngine
from config import settings as cfg

broker = MT5Broker()
if not broker.connect():
    print("Cannot connect"); sys.exit(1)

mt5.symbol_select("EURCAD", True)
df, params = download_data(broker, "EURCAD", timeframe="M5", bars=20000)
broker.disconnect()

if df is None:
    print("No data"); sys.exit(1)

# Check that time column is preserved for weekend detection
has_time = "time" in df.columns
print("Data: %d bars, time column: %s" % (len(df), "YES" if has_time else "NO"))
if has_time:
    print("  From: %s" % df["time"].iloc[0])
    print("  To:   %s" % df["time"].iloc[-1])
    # Count weekends
    fridays = df[df["time"].dt.weekday == 4]
    fri_21 = fridays[fridays["time"].dt.hour >= 21]
    weekends = len(df[df["time"].dt.weekday >= 5])
    print("  Friday close bars (21:00+ UTC): %d" % len(fri_21))
    print("  Weekend bars: %d" % weekends)

print()
print("=" * 80)
print("  REALISTIC BACKTEST: EURCAD with Weekend Protection")
print("  0.08 lots, $1,000 capital, real OANDA data, all production features")
print("=" * 80)

# Run 1: WITH weekend wind-down (production config)
print("\n  Running WITH weekend wind-down (threshold=%.1f spacings)..." % cfg.WEEKEND_CLOSE_THRESHOLD_SPACINGS)
engine1 = GridBacktestEngine(
    df, params["contract_size"], params["tick_size"],
    commission_per_lot=0.0, bar_seconds=300.0,
    weekend_close_threshold=cfg.WEEKEND_CLOSE_THRESHOLD_SPACINGS,
    vol_min=0.01,
)
r1 = engine1.run(starting_equity=1000.0, slip_ticks=1)

# Run 2: WITHOUT weekend wind-down (for comparison)
print("  Running WITHOUT weekend wind-down (original)...")
engine2 = GridBacktestEngine(
    df, params["contract_size"], params["tick_size"],
    commission_per_lot=0.0, bar_seconds=300.0,
    weekend_close_threshold=0.0,  # disabled
    vol_min=0.01,
)
r2 = engine2.run(starting_equity=1000.0, slip_ticks=1)

# Display comparison
print()
print("  %30s  %15s  %15s" % ("Metric", "WITH Weekend", "WITHOUT Weekend"))
print("  " + "-" * 65)
print("  %30s  $%14.2f  $%14.2f" % ("Net PnL", r1.total_pnl_net, r2.total_pnl_net))
print("  %30s  $%14.2f  $%14.2f" % ("Realized PnL", r1.total_pnl, r2.total_pnl))
print("  %30s  %14d  %14d" % ("Trades", r1.trades, r2.trades))
print("  %30s  %14d  %14d" % ("Wins", r1.wins, r2.wins))
print("  %30s  %14d  %14d" % ("Losses", r1.losses, r2.losses))
wr1 = r1.wins / max(r1.trades, 1) * 100
wr2 = r2.wins / max(r2.trades, 1) * 100
print("  %30s  %13.1f%%  %13.1f%%" % ("Win Rate", wr1, wr2))
print("  %30s  %13.1f%%  %13.1f%%" % ("Max Drawdown", r1.max_drawdown, r2.max_drawdown))
print("  %30s  %14.2f  %14.2f" % ("Sharpe", r1.sharpe, r2.sharpe))
print("  %30s  %14.2f  %14.2f" % ("Profit Factor", r1.profit_factor, r2.profit_factor))
print("  %30s  %14.3f  %14.3f" % ("Max Inventory", r1.max_inventory, r2.max_inventory))

diff = r1.total_pnl_net - r2.total_pnl_net
print()
print("  WEEKEND PROTECTION COST: $%.2f over the backtest period" % abs(diff))

# Monthly breakdown with weekend protection
if has_time:
    import numpy as np
    df_bt = df.copy()
    df_bt["month"] = df_bt["time"].dt.to_period("M")
    months = sorted(df_bt["month"].unique())

    print()
    print("  MONTHLY BREAKDOWN (with weekend protection):")
    print("  %10s  %10s  %10s  %6s  %6s  %6s" % (
        "Month", "Net PnL", "Realized", "Trades", "WR", "MaxDD"))
    print("  " + "-" * 55)

    monthly_pnls = []
    for month in months:
        chunk = df_bt[df_bt["month"] == month].reset_index(drop=True)
        if len(chunk) < 200:
            continue
        engine = GridBacktestEngine(
            chunk, params["contract_size"], params["tick_size"],
            commission_per_lot=0.0, bar_seconds=300.0,
            weekend_close_threshold=cfg.WEEKEND_CLOSE_THRESHOLD_SPACINGS,
            vol_min=0.01,
        )
        r = engine.run(starting_equity=1000.0, slip_ticks=1)
        wr = r.wins / max(r.trades, 1) * 100
        monthly_pnls.append(r.total_pnl_net)
        verdict = "PROFIT" if r.total_pnl_net > 0 else "LOSS"
        print("  %10s  $%+9.2f  $%+9.2f  %5d  %4.0f%%  %5.1f%%  %s" % (
            str(month), r.total_pnl_net, r.total_pnl, r.trades,
            wr, r.max_drawdown, verdict))

    if monthly_pnls:
        pos = sum(1 for p in monthly_pnls if p > 0)
        avg = np.mean(monthly_pnls)
        print("  " + "-" * 55)
        print("  Profitable months: %d/%d (%.0f%%)" % (pos, len(monthly_pnls), pos/len(monthly_pnls)*100))
        print("  Average monthly:   $%+.2f" % avg)
        print("  Best month:        $%+.2f" % max(monthly_pnls))
        print("  Worst month:       $%+.2f" % min(monthly_pnls))
        print("  Total:             $%+.2f" % sum(monthly_pnls))

print()
print("=" * 80)
