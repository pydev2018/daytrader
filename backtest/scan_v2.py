"""Run the improved scanner v2 on real OANDA data and compare with backtest results."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BT_COMMISSION_PER_LOT'] = '0'
os.environ['RUNG_STOP_LOSS_SPACINGS'] = '0'
os.environ['BASE_ORDER_SIZE_LOTS'] = '0.10'

import MetaTrader5 as mt5
from brokers.mt5 import MT5Broker
from grid.scanner import GridScanner, FX_UNIVERSE
from backtest.real_data_backtest import download_data, resolve_symbol, OANDA_SYMBOL_MAP
from backtest.grid_engine import GridBacktestEngine

broker = MT5Broker()
if not broker.connect():
    print("Cannot connect"); sys.exit(1)

# Resolve and enable all symbols
available = []
for base in FX_UNIVERSE:
    real = resolve_symbol(base)
    info = mt5.symbol_info(real)
    if info is not None:
        if not info.visible:
            mt5.symbol_select(real, True)
        rates = mt5.copy_rates_from_pos(real, mt5.TIMEFRAME_M5, 0, 10)
        if rates is not None and len(rates) > 0:
            available.append(real)

print(f"Scanning {len(available)} symbols with improved scanner v2...\n")

scanner = GridScanner(broker)
results = scanner.scan(available, timeframe="M5", bar_count=2000)

# Now download data and backtest each to validate scanner rankings
print("=" * 95)
print("  SCANNER v2 RESULTS + BACKTEST VALIDATION")
print("  Does the new scanner correctly rank symbols by actual profitability?")
print("=" * 95)

print(f"\n  {'#':>3} {'Symbol':<14} {'Score':>6} {'Verdict':<10} "
      f"{'OscRatio':>8} {'Cost%':>6} {'Hurst':>6} {'FillsDay':>9} "
      f"{'BacktestPnL':>12} {'DD':>6}")
print("  " + "-" * 90)

backtest_data = []
for i, r in enumerate(results, 1):
    sym = r.symbol
    df, params = download_data(broker, sym, timeframe="M5", bars=20000)
    
    bt_pnl = "N/A"
    bt_dd = "N/A"
    if df is not None:
        engine = GridBacktestEngine(
            df, params["contract_size"], params["tick_size"],
            commission_per_lot=0.0, bar_seconds=300.0,
        )
        bt = engine.run(starting_equity=1000.0, slip_ticks=1)
        bt_pnl = f"${bt.total_pnl_net:>+10.2f}"
        bt_dd = f"{bt.max_drawdown:>5.1f}%"
        backtest_data.append((sym, r.score, bt.total_pnl_net, bt.max_drawdown, bt.trades))
    
    d = r.details
    print(f"  {i:>3} {sym:<14} {r.score:>6.1f} {r.verdict:<10} "
          f"{d.get('oscillation_ratio', 0):>8.1f} {d.get('cost_ratio_pct', 0):>5.1f}% "
          f"{d.get('hurst', 0):>6.3f} {d.get('est_fills_per_day', 0):>8.1f} "
          f"{bt_pnl:>12} {bt_dd:>6}")

broker.disconnect()

# Correlation analysis
if len(backtest_data) >= 5:
    import numpy as np
    scores = np.array([d[1] for d in backtest_data])
    pnls = np.array([d[2] for d in backtest_data])
    dds = np.array([d[3] for d in backtest_data])
    
    # Rank correlation (Spearman)
    from scipy.stats import spearmanr
    corr_pnl, p_pnl = spearmanr(scores, pnls)
    corr_dd, p_dd = spearmanr(scores, -dds)  # negative DD = better
    
    print(f"\n  SCANNER QUALITY METRICS:")
    print(f"    Score-PnL rank correlation:  {corr_pnl:>+.3f}  (p={p_pnl:.3f})")
    print(f"    Score-DD rank correlation:   {corr_dd:>+.3f}  (p={p_dd:.3f})")
    if corr_pnl > 0.3:
        print(f"    => Scanner positively predicts profitability!")
    elif corr_pnl < -0.1:
        print(f"    => Scanner is inversely correlated — needs more tuning")
    else:
        print(f"    => Weak correlation — scanner has some signal but noisy")

    # Top 5 vs Bottom 5
    sorted_by_score = sorted(backtest_data, key=lambda x: x[1], reverse=True)
    top5 = sorted_by_score[:5]
    bot5 = sorted_by_score[-5:]
    
    top5_pnl = sum(d[2] for d in top5)
    bot5_pnl = sum(d[2] for d in bot5)
    top5_dd = max(d[3] for d in top5)
    bot5_dd = max(d[3] for d in bot5)
    
    print(f"\n  TOP 5 vs BOTTOM 5 (0.10 lots, $1000 capital, 100 days):")
    print(f"    Top 5 total PnL:  ${top5_pnl:>+10.2f}   worst DD: {top5_dd:.1f}%")
    print(f"    Bot 5 total PnL:  ${bot5_pnl:>+10.2f}   worst DD: {bot5_dd:.1f}%")
    print(f"    Scanner edge:     ${top5_pnl - bot5_pnl:>+10.2f}")
    
    # Best single symbol recommendation
    best = max(backtest_data, key=lambda x: x[2])
    best_by_calmar = max(backtest_data, key=lambda x: x[2] / max(x[3], 0.1))
    print(f"\n  RECOMMENDATIONS:")
    print(f"    Highest PnL:           {best[0]:<14} ${best[2]:>+.2f} (DD={best[3]:.1f}%)")
    print(f"    Best risk-adjusted:    {best_by_calmar[0]:<14} ${best_by_calmar[2]:>+.2f} (DD={best_by_calmar[3]:.1f}%)")
    print(f"    Scanner's #1 pick:     {backtest_data[0][0]:<14} ${backtest_data[0][2]:>+.2f} (DD={backtest_data[0][3]:.1f}%)")
