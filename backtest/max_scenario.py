"""
Maximum scenario: 0.10 lots, $1000 capital, real OANDA data.
Original strategy (no recycling tricks).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BT_COMMISSION_PER_LOT'] = '0'
os.environ['RUNG_STOP_LOSS_SPACINGS'] = '0'
os.environ['BASE_ORDER_SIZE_LOTS'] = '0.10'

import MetaTrader5 as mt5
from brokers.mt5 import MT5Broker
from backtest.real_data_backtest import download_data, resolve_symbol
from backtest.grid_engine import GridBacktestEngine
from config import settings as cfg

broker = MT5Broker()
if not broker.connect():
    print("Cannot connect"); sys.exit(1)

# Download data for all symbols first
symbols = ["EURCAD", "AUDNZD", "EURCHF", "EURAUD", "EURGBP",
           "NZDUSD", "GBPCHF", "EURUSD", "NZDCAD", "GBPUSD"]

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

print()
print("=" * 75)
print("  MAXIMUM SCENARIO: $1,000 capital, 0.10 lots, original strategy")
print("  Real OANDA data, ~100 trading days, no tricks")
print("=" * 75)

print(f"\n  Settings: lots={cfg.BASE_ORDER_SIZE_LOTS}  levels={cfg.GRID_LEVELS}  "
      f"anchor_hl={cfg.ANCHOR_HALFLIFE_SECONDS}s  commission=$0")

print(f"\n  {'Symbol':<14} {'Net PnL':>10} {'Realized':>10} {'Unrealzd':>10} "
      f"{'Trades':>7} {'WR':>5} {'MaxDD':>7} {'MaxInv':>8}")
print("  " + "-" * 73)

total_net = 0
total_realized = 0
total_trades = 0
worst_dd = 0
combined_equity = None

for real, (df, params) in data_cache.items():
    engine = GridBacktestEngine(
        df, params["contract_size"], params["tick_size"],
        commission_per_lot=0.0, bar_seconds=300.0,
    )
    result = engine.run(starting_equity=1000.0, slip_ticks=1)

    unrealized = result.total_pnl_net - result.total_pnl
    wr = result.wins / max(result.trades, 1) * 100
    total_net += result.total_pnl_net
    total_realized += result.total_pnl
    total_trades += result.trades
    worst_dd = max(worst_dd, result.max_drawdown)

    # Combine equity curves
    if combined_equity is None:
        combined_equity = [1000.0 + (e - 1000.0) for e in result.equity_curve]
    else:
        for i in range(min(len(combined_equity), len(result.equity_curve))):
            combined_equity[i] += (result.equity_curve[i] - 1000.0)

    print(f"  {real:<14} ${result.total_pnl_net:>9.2f} ${result.total_pnl:>9.2f} "
          f"${unrealized:>9.2f} {result.trades:>7} {wr:>4.0f}% "
          f"{result.max_drawdown:>6.1f}% {result.max_inventory:>7.3f}")

print("  " + "-" * 73)
total_unrealized = total_net - total_realized
print(f"  {'TOTAL':<14} ${total_net:>9.2f} ${total_realized:>9.2f} "
      f"${total_unrealized:>9.2f} {total_trades:>7}")

# Portfolio stats
if combined_equity:
    peak = max(combined_equity)
    trough = min(combined_equity)
    final = combined_equity[-1]
    portfolio_dd = 0
    running_peak = combined_equity[0]
    for e in combined_equity:
        if e > running_peak:
            running_peak = e
        dd = (running_peak - e) / running_peak * 100
        portfolio_dd = max(portfolio_dd, dd)

    print(f"\n  PORTFOLIO SUMMARY (all {len(data_cache)} symbols combined)")
    print(f"  Starting capital:  $1,000.00")
    print(f"  Final equity:      ${final:>10.2f}")
    print(f"  Total return:      {(final/1000-1)*100:>+10.1f}%")
    print(f"  Peak equity:       ${peak:>10.2f}")
    print(f"  Portfolio max DD:  {portfolio_dd:>10.1f}%")
    print(f"  Total trades:      {total_trades:>10}")
    print(f"  Realized P&L:      ${total_realized:>10.2f}")
    print(f"  Unrealized end:    ${total_unrealized:>10.2f}")
    
    days = 100
    per_month = total_net / days * 22
    print(f"\n  Monthly estimate:  ${per_month:>10.2f}")
    print(f"  Monthly return:    {per_month/1000*100:>10.1f}%")
    if per_month > 0:
        months_to_double = 1000 / per_month
        print(f"  Time to $2,000:    {months_to_double:>10.1f} months")

    # Simple equity curve
    print(f"\n  Equity curve (portfolio):")
    step = max(1, len(combined_equity) // 50)
    sampled = combined_equity[::step]
    mn, mx = min(sampled), max(sampled)
    rng = mx - mn if mx > mn else 1
    for row in range(10, -1, -1):
        threshold = mn + (row / 10) * rng
        label = f"${threshold:>8,.0f}" if row % 2 == 0 else " " * 9
        line = ""
        for val in sampled:
            line += "#" if val >= threshold else " "
        print(f"  {label} |{line}|")
    print(f"  {'':>9} day 0{' ' * (len(sampled) - 10)}day ~100")

print("\n" + "=" * 75)
