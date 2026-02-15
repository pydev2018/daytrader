"""
Test different GRID_LEVELS on real EURCAD data.
Answers: is 6 the right number, or would 3, 4, 8, 10 be better?
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BT_COMMISSION_PER_LOT'] = '0'
os.environ['RUNG_STOP_LOSS_SPACINGS'] = '0'

import MetaTrader5 as mt5
from brokers.mt5 import MT5Broker
from backtest.real_data_backtest import download_data
from backtest.grid_engine import GridBacktestEngine
import importlib

broker = MT5Broker()
if not broker.connect():
    print("Cannot connect"); sys.exit(1)

mt5.symbol_select("EURCAD", True)
df, params = download_data(broker, "EURCAD", timeframe="M5", bars=20000)
broker.disconnect()

if df is None:
    print("No data"); sys.exit(1)

print("Downloaded %d bars of EURCAD M5 data" % len(df))
print()
print("=" * 85)
print("  GRID LEVELS TEST: EURCAD, 0.08 lots, $1,000 capital, real OANDA data")
print("  Which number of levels gives the best risk-adjusted return?")
print("=" * 85)

print()
print("  Levels  Net PnL  Realized  Trades  MaxDD  MaxInv  Calmar  Monthly")
print("  " + "-" * 75)

results = []
for levels in [2, 3, 4, 5, 6, 7, 8, 10, 12]:
    # Override config for this test
    os.environ['GRID_LEVELS'] = str(levels)
    os.environ['BASE_ORDER_SIZE_LOTS'] = '0.08'
    import config.settings
    importlib.reload(config.settings)
    
    engine = GridBacktestEngine(
        df, params["contract_size"], params["tick_size"],
        commission_per_lot=0.0, bar_seconds=300.0,
    )
    r = engine.run(starting_equity=1000.0, slip_ticks=1)
    
    calmar = r.total_pnl_net / max(r.max_drawdown, 0.1)
    monthly = r.total_pnl_net / 100 * 22  # ~100 trading days in data
    margin_worst = levels * 63.62  # $63.62 per position at 0.08 lots
    margin_level = 1000 / max(margin_worst, 1) * 100
    
    marker = ""
    if margin_level < 200:
        marker = " MARGIN RISK!"
    elif calmar > 5 and r.total_pnl_net > 0:
        marker = " <<<"
    
    print("  %4d   $%+8.2f $%+8.2f  %5d  %5.1f%%  %5.3f  %6.1f  $%+7.0f  ML=%3.0f%%%s" % (
        levels, r.total_pnl_net, r.total_pnl, r.trades,
        r.max_drawdown, r.max_inventory, calmar, monthly,
        margin_level, marker))
    
    results.append({
        "levels": levels,
        "pnl": r.total_pnl_net,
        "realized": r.total_pnl,
        "trades": r.trades,
        "dd": r.max_drawdown,
        "max_inv": r.max_inventory,
        "calmar": calmar,
        "monthly": monthly,
        "margin_level": margin_level,
    })

# Find best risk-adjusted (Calmar) that's margin-safe
safe = [r for r in results if r["margin_level"] >= 200 and r["pnl"] > 0]
if safe:
    best = max(safe, key=lambda x: x["calmar"])
    most_profit = max(safe, key=lambda x: x["pnl"])
    print()
    print("  RESULTS:")
    print("    Best risk-adjusted (Calmar): %d levels" % best["levels"])
    print("      PnL=$%+.2f  DD=%.1f%%  Monthly=$%+.0f" % (best["pnl"], best["dd"], best["monthly"]))
    print("    Most profitable (margin-safe): %d levels" % most_profit["levels"])
    print("      PnL=$%+.2f  DD=%.1f%%  Monthly=$%+.0f" % (most_profit["pnl"], most_profit["dd"], most_profit["monthly"]))

# Reset config
os.environ['GRID_LEVELS'] = '6'
os.environ['BASE_ORDER_SIZE_LOTS'] = '0.08'
importlib.reload(config.settings)
