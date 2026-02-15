"""
Full real-data backtest pipeline:
1. Connect to MT5
2. Run scanner on all available symbols
3. Download real M5 historical data
4. Run backtests on scanner's TOP picks vs BOTTOM picks
5. Show whether the scanner adds value
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ['BT_COMMISSION_PER_LOT'] = '0'
os.environ['RUNG_STOP_LOSS_SPACINGS'] = '0'

import time
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from config import settings as cfg
from brokers.mt5 import MT5Broker, TF_MAP
from grid.scanner import GridScanner, FX_UNIVERSE
from backtest.grid_engine import GridBacktestEngine, BacktestResult

# OANDA-specific symbol names (discovered via test_mt5_data.py)
OANDA_SYMBOL_MAP = {
    "EURUSD": "EURUSD.sml", "GBPUSD": "GBPUSD.sml", "USDJPY": "USDJPY.sml",
    "AUDUSD": "AUDUSD.sml", "EURGBP": "EURGBP.sml", "GBPJPY": "GBPJPY.sml",
    "USDCHF": "USDCHF", "USDCAD": "USDCAD", "NZDUSD": "NZDUSD",
    "EURJPY": "EURJPY", "EURCHF": "EURCHF", "EURAUD": "EURAUD",
    "EURCAD": "EURCAD", "EURNZD": "EURNZD", "GBPCHF": "GBPCHF",
    "GBPAUD": "GBPAUD", "GBPCAD": "GBPCAD", "GBPNZD": "GBPNZD",
    "AUDJPY": "AUDJPY", "AUDCHF": "AUDCHF", "AUDCAD": "AUDCAD",
    "AUDNZD": "AUDNZD", "NZDJPY": "NZDJPY", "NZDCHF": "NZDCHF",
    "NZDCAD": "NZDCAD", "CADJPY": "CADJPY", "CADCHF": "CADCHF",
    "CHFJPY": "CHFJPY",
}

def resolve_symbol(base_name: str) -> str:
    """Resolve a base pair name to the OANDA-specific symbol name."""
    return OANDA_SYMBOL_MAP.get(base_name, base_name)


def download_data(broker, symbol, timeframe="M5", bars=5000):
    """Download historical bid-side OHLC and reconstruct ask from spread."""
    if not broker.select_symbol(symbol):
        return None, None
    
    sym_info = broker.symbol_info(symbol)
    if sym_info is None:
        return None, None

    tf = TF_MAP.get(timeframe, mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) < 50:
        return None, None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)

    point = sym_info.get("point", 0.00001)
    tick_size = sym_info.get("trade_tick_size", point)
    contract_size = sym_info.get("trade_contract_size", 100000)
    digits = sym_info.get("digits", 5)

    # MT5 provides spread in points for each bar
    if "spread" in df.columns:
        spread_price = df["spread"].values * point
    else:
        # Fallback: use a typical spread
        spread_price = np.full(len(df), 15 * point)

    # Reconstruct bid/ask OHLC
    df["bid_open"] = df["open"]
    df["bid_high"] = df["high"]
    df["bid_low"] = df["low"]
    df["bid_close"] = df["close"]
    df["ask_open"] = df["open"] + spread_price
    df["ask_high"] = df["high"] + spread_price
    df["ask_low"] = df["low"] + spread_price
    df["ask_close"] = df["close"] + spread_price
    df["mid_close"] = (df["bid_close"] + df["ask_close"]) / 2.0

    params = {
        "point": point,
        "tick_size": tick_size,
        "contract_size": contract_size,
        "digits": digits,
    }
    return df, params


def run_backtest_on_symbol(df, params, bar_seconds=300.0, equity=10000.0):
    """Run grid backtest and return result."""
    engine = GridBacktestEngine(
        df,
        contract_size=params["contract_size"],
        tick_size=params["tick_size"],
        commission_per_lot=0.0,
        bar_seconds=bar_seconds,
    )
    return engine.run(
        starting_equity=equity,
        slip_ticks=1,
        spread_mult=1.0,
        use_regime_filter=True,
    )


def main():
    print()
    print("=" * 80)
    print("  REAL DATA BACKTEST PIPELINE")
    print("  Scanner Quality Test: Do scanner picks outperform?")
    print("=" * 80)

    # ── Connect ──────────────────────────────────────────────────────────
    broker = MT5Broker()
    if not broker.connect():
        print("ERROR: Cannot connect to MT5")
        return

    acc = broker.account_info()
    print(f"\n  Account: {acc.get('login')}  Balance: ${acc.get('balance', 0):,.2f}")

    # ── Enable all symbols in MarketWatch ────────────────────────────────
    print(f"\n  Enabling symbols in MarketWatch...")
    available = []
    base_to_real = {}  # track mapping for display
    for base in FX_UNIVERSE:
        real = resolve_symbol(base)
        info = mt5.symbol_info(real)
        if info is not None:
            if not info.visible:
                mt5.symbol_select(real, True)
                time.sleep(0.1)
            rates = mt5.copy_rates_from_pos(real, mt5.TIMEFRAME_M5, 0, 10)
            if rates is not None and len(rates) > 0:
                available.append(real)
                base_to_real[base] = real

    print(f"  {len(available)} symbols have historical data")

    if len(available) < 5:
        print("  Not enough symbols with data. Try adding symbols to MarketWatch in MT5.")
        broker.disconnect()
        return

    # ── Step 1: Run Scanner ──────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("  STEP 1: SCANNING ALL AVAILABLE SYMBOLS")
    print("=" * 80)

    scanner = GridScanner(broker)
    results = scanner.scan(available, timeframe="M5", bar_count=2000)

    if not results:
        print("  Scanner returned no results")
        broker.disconnect()
        return

    print(f"\n  {'#':>3}  {'Symbol':<10} {'Score':>6} {'Verdict':<10} "
          f"{'Hurst':>6} {'VR':>6} {'Cost%':>6} {'TrendZ':>7}")
    print("  " + "-" * 70)
    for i, r in enumerate(results, 1):
        d = r.details
        print(f"  {i:>3}  {r.symbol:<10} {r.score:>6.1f} {r.verdict:<10} "
              f"{d.get('hurst', 0):>6.3f} {d.get('variance_ratio', 0):>6.3f} "
              f"{d.get('cost_ratio_pct', 0):>5.1f}% {d.get('trend_z', 0):>+7.2f}")

    # ── Step 2: Download Data and Backtest ───────────────────────────────
    print(f"\n{'=' * 80}")
    print("  STEP 2: DOWNLOADING DATA & RUNNING BACKTESTS")
    print("=" * 80)

    # Determine top and bottom picks
    n_test = min(5, len(results) // 2)
    top_picks = results[:n_test]
    bottom_picks = results[-n_test:]

    all_results = []

    for group_name, picks in [("TOP (Scanner's Best)", top_picks),
                                ("BOTTOM (Scanner's Worst)", bottom_picks)]:
        print(f"\n  --- {group_name} ---")
        group_pnls = []

        for score in picks:
            sym = score.symbol
            print(f"  Downloading {sym}...", end=" ", flush=True)
            df, params = download_data(broker, sym, timeframe="M5", bars=20000)
            if df is None:
                print("NO DATA")
                continue

            print(f"{len(df)} bars.", end=" ", flush=True)

            # Run backtest
            result = run_backtest_on_symbol(df, params, bar_seconds=300.0)

            wr = result.wins / max(result.trades, 1) * 100
            print(f"PnL={result.total_pnl_net:>+10.2f}  "
                  f"Trades={result.trades:>4}  WR={wr:.0f}%  "
                  f"DD={result.max_drawdown:.1f}%")

            group_pnls.append(result.total_pnl_net)
            all_results.append({
                "group": group_name,
                "symbol": sym,
                "score": score.score,
                "pnl_net": result.total_pnl_net,
                "trades": result.trades,
                "win_rate": wr,
                "max_dd": result.max_drawdown,
                "sharpe": result.sharpe,
                "realized": result.total_pnl,
                "max_inv": result.max_inventory,
            })

        if group_pnls:
            avg = sum(group_pnls) / len(group_pnls)
            pos = sum(1 for p in group_pnls if p > 0)
            print(f"\n  {group_name} Summary:")
            print(f"    Avg PnL: ${avg:>+.2f}")
            print(f"    Positive: {pos}/{len(group_pnls)}")
            print(f"    Total: ${sum(group_pnls):>+.2f}")

    # ── Step 3: Comparison ───────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("  STEP 3: SCANNER QUALITY COMPARISON")
    print("=" * 80)

    top_group = [r for r in all_results if "TOP" in r["group"]]
    bot_group = [r for r in all_results if "BOTTOM" in r["group"]]

    if top_group and bot_group:
        top_avg = sum(r["pnl_net"] for r in top_group) / len(top_group)
        bot_avg = sum(r["pnl_net"] for r in bot_group) / len(bot_group)
        top_total = sum(r["pnl_net"] for r in top_group)
        bot_total = sum(r["pnl_net"] for r in bot_group)

        print(f"\n  {'Metric':<25} {'Top Picks':>15} {'Bottom Picks':>15}")
        print("  " + "-" * 55)
        print(f"  {'Avg PnL per symbol':<25} ${top_avg:>14.2f} ${bot_avg:>14.2f}")
        print(f"  {'Total PnL':<25} ${top_total:>14.2f} ${bot_total:>14.2f}")
        print(f"  {'Avg Trades':<25} "
              f"{sum(r['trades'] for r in top_group)/len(top_group):>15.0f} "
              f"{sum(r['trades'] for r in bot_group)/len(bot_group):>15.0f}")
        print(f"  {'Avg Max DD':<25} "
              f"{sum(r['max_dd'] for r in top_group)/len(top_group):>14.1f}% "
              f"{sum(r['max_dd'] for r in bot_group)/len(bot_group):>14.1f}%")

        diff = top_avg - bot_avg
        print(f"\n  Scanner edge: ${diff:>+.2f} per symbol (top vs bottom)")
        if diff > 0:
            print("  CONCLUSION: Scanner picks outperform. The scanner adds value.")
        else:
            print("  CONCLUSION: Scanner picks did not outperform in this window.")
            print("  This could mean: (a) the data window was too short,")
            print("  (b) all symbols were in similar regime, or")
            print("  (c) scanner needs tuning.")

    # ── Full detail table ────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("  FULL RESULTS TABLE")
    print("=" * 80)
    print(f"\n  {'Symbol':<10} {'Group':<8} {'Score':>6} {'PnL':>10} "
          f"{'Trades':>6} {'WR':>5} {'DD':>6} {'Realized':>10} {'MaxInv':>7}")
    print("  " + "-" * 75)
    for r in sorted(all_results, key=lambda x: x["score"], reverse=True):
        g = "TOP" if "TOP" in r["group"] else "BOT"
        print(f"  {r['symbol']:<10} {g:<8} {r['score']:>6.1f} ${r['pnl_net']:>9.2f} "
              f"{r['trades']:>6} {r['win_rate']:>4.0f}% {r['max_dd']:>5.1f}% "
              f"${r['realized']:>9.2f} {r['max_inv']:>6.3f}")

    broker.disconnect()
    print(f"\n  Done. MT5 disconnected.")


if __name__ == "__main__":
    main()
