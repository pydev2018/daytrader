"""Deep diagnosis of where the money is lost."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BT_COMMISSION_PER_LOT'] = '0'

import numpy as np
from backtest.run_backtest import generate_synthetic
from backtest.grid_engine import GridBacktestEngine
from config import settings as cfg

# Run mixed backtest and track inventory over time
df, params = generate_synthetic("EURUSD", 10000, 60.0, "mixed", 42)
engine = GridBacktestEngine(df, params["contract_size"], params["tick_size"],
                            commission_per_lot=0.0, bar_seconds=60.0)
result = engine.run(starting_equity=10000.0, slip_ticks=1)

print("=== TRADE ANALYSIS ===")
print(f"Total trades: {result.trades}")
print(f"Win rate: {result.wins/max(result.trades,1)*100:.1f}%")
print(f"Total gross PnL: ${result.total_pnl:.2f}")
print(f"Total net PnL: ${result.total_pnl_net:.2f}")

if result.trade_records:
    pnls = [t.pnl_net for t in result.trade_records]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    print(f"\nWinners: {len(winners)}")
    if winners:
        print(f"  Avg win: ${np.mean(winners):.4f}")
        print(f"  Total wins: ${sum(winners):.2f}")
    print(f"Losers: {len(losers)}")
    if losers:
        print(f"  Avg loss: ${np.mean(losers):.4f}")
        print(f"  Total losses: ${sum(losers):.2f}")

# The real PnL breakdown: realized vs unrealized
eq = result.equity_curve
peak = max(eq)
final = eq[-1]
print(f"\n=== EQUITY BREAKDOWN ===")
print(f"Peak equity: ${peak:.2f}")
print(f"Final equity: ${final:.2f}")
print(f"Max DD from peak: {(peak-min(eq[eq.index(peak):]))/peak*100:.1f}%" if peak in eq else "N/A")

# The issue: what % of time are we holding losing inventory?
print(f"\n=== THE CORE PROBLEM ===")
print(f"Realized PnL (closed trades): ${result.total_pnl:.2f}")
print(f"Final equity - starting - realized = unrealized at end")
unrealized_end = final - 10000.0 - result.total_pnl + result.total_commission + result.total_swap
print(f"Unrealized at end: ${unrealized_end:.2f}")
print()
print("Grid strategies have 100% win rate on CLOSED trades")
print("(every entry + exit = 1 spacing of profit)")
print()
print("The loss comes from OPEN positions that haven't exited yet.")
print("When price trends away, entries fill but exits never fill.")
print("The unrealized loss on those open positions = our drawdown.")
print()
print(f"Max inventory seen: {result.max_inventory:.4f} lots")
print(f"At 100k contract, 1 pip move on {result.max_inventory:.4f} lots = ${result.max_inventory * 100000 * 0.00001:.4f}")
print(f"If price moves 50 pips against us: ${result.max_inventory * 100000 * 0.0005:.2f} unrealized loss")
print()
print("SOLUTION: The strategy needs to be more aggressive about")
print("cutting losing inventory during trends, not just pausing.")
