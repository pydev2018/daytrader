"""Test different recycle thresholds to find the sweet spot.
Downloads data first, disconnects MT5, then runs all backtests offline."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BT_COMMISSION_PER_LOT'] = '0'
os.environ['RUNG_STOP_LOSS_SPACINGS'] = '0'

import MetaTrader5 as mt5
from brokers.mt5 import MT5Broker
from backtest.real_data_backtest import download_data, resolve_symbol
from backtest.grid_engine import GridBacktestEngine
from backtest.hedge_test import RecyclingGridBacktest

# ── Download all data first, then disconnect ────────────────────────────
broker = MT5Broker()
if not broker.connect():
    print("Cannot connect"); sys.exit(1)

symbols = ["EURCAD", "EURCHF", "NZDUSD", "AUDNZD", "EURAUD",
           "EURUSD", "EURGBP", "GBPCHF", "NZDCAD", "GBPUSD"]

print("Downloading data...")
data_cache = {}
for base in symbols:
    real = resolve_symbol(base)
    mt5.symbol_select(real, True)
    df, params = download_data(broker, real, timeframe="M5", bars=20000)
    if df is not None:
        data_cache[real] = (df, params)
        print(f"  {real}: {len(df)} bars")

broker.disconnect()
print(f"Downloaded {len(data_cache)} symbols. MT5 disconnected.\n")

# ── Run backtests offline ───────────────────────────────────────────────
print("=" * 85)
print("  RECYCLE THRESHOLD TUNING (real OANDA data, 20k M5 bars)")
print("=" * 85)

thresholds = [None, 3.0, 4.0, 5.0, 6.0, 8.0]
short_names = {k: k.replace(".sml", "")[:7] for k in data_cache}

print(f"\n  {'Threshold':<12}", end="")
for real in data_cache:
    print(f" {short_names[real]:>9}", end="")
print(f" {'TOTAL':>10}")
print("  " + "-" * (12 + 10 * (len(data_cache) + 1)))

for thresh in thresholds:
    label = "Original" if thresh is None else f"Recycle {thresh:.0f}x"
    print(f"  {label:<12}", end="", flush=True)
    total = 0.0

    for real, (df, params) in data_cache.items():
        if thresh is None:
            engine = GridBacktestEngine(
                df, params["contract_size"], params["tick_size"],
                commission_per_lot=0.0, bar_seconds=300.0,
            )
            result = engine.run(starting_equity=10000.0, slip_ticks=1)
        else:
            engine = RecyclingGridBacktest(
                df, params["contract_size"], params["tick_size"],
                bar_seconds=300.0, recycle_threshold=thresh,
            )
            result, _, _ = engine.run(starting_equity=10000.0, slip_ticks=1)

        total += result.total_pnl_net
        print(f"  {result.total_pnl_net:>+8.2f}", end="", flush=True)

    marker = " <<<" if thresh is None else (" BEST" if total > 350 else "")
    print(f"  {total:>+9.2f}{marker}")

print()
