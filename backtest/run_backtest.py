"""
Grid Backtest Runner — complete script to run and analyze backtests.

Three data modes:
  1. MT5 LIVE DATA  — downloads real bid/ask bars from MT5 (market must be open)
  2. CSV FILE       — loads bid/ask bars from a CSV file
  3. SYNTHETIC      — generates realistic simulated data (always works)

Usage:
    # Synthetic data (works anytime, no MT5 needed):
    python -m backtest.run_backtest --synthetic --symbol EURUSD --bars 10000

    # From MT5 (market must be open):
    python -m backtest.run_backtest --mt5 --symbol EURUSD --timeframe M1 --bars 5000

    # From CSV:
    python -m backtest.run_backtest --csv path/to/data.csv

    # Walk-forward validation:
    python -m backtest.run_backtest --synthetic --symbol EURUSD --bars 20000 --walk-forward 5

    # Stress test (2x spread, 2 ticks slippage):
    python -m backtest.run_backtest --synthetic --spread-mult 2.0 --slip 2

    # Save results to CSV:
    python -m backtest.run_backtest --synthetic --save
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings as cfg
from backtest.grid_engine import GridBacktestEngine, BacktestResult, walk_forward
from backtest.data import normalize_bars


# ═════════════════════════════════════════════════════════════════════════════
#  SYNTHETIC DATA GENERATORS
# ═════════════════════════════════════════════════════════════════════════════

# Realistic parameters for common FX pairs
SYMBOL_PARAMS = {
    "EURUSD": {
        "center": 1.08500, "ann_vol": 0.07, "spread_pips": 1.2,
        "point": 0.00001, "contract_size": 100_000, "tick_size": 0.00001,
    },
    "GBPUSD": {
        "center": 1.26500, "ann_vol": 0.09, "spread_pips": 1.5,
        "point": 0.00001, "contract_size": 100_000, "tick_size": 0.00001,
    },
    "USDJPY": {
        "center": 150.500, "ann_vol": 0.10, "spread_pips": 1.5,
        "point": 0.001, "contract_size": 100_000, "tick_size": 0.001,
    },
    "EURGBP": {
        "center": 0.85800, "ann_vol": 0.06, "spread_pips": 1.3,
        "point": 0.00001, "contract_size": 100_000, "tick_size": 0.00001,
    },
    "AUDNZD": {
        "center": 1.08200, "ann_vol": 0.06, "spread_pips": 2.0,
        "point": 0.00001, "contract_size": 100_000, "tick_size": 0.00001,
    },
    "AUDUSD": {
        "center": 0.65500, "ann_vol": 0.08, "spread_pips": 1.4,
        "point": 0.00001, "contract_size": 100_000, "tick_size": 0.00001,
    },
    "USDCAD": {
        "center": 1.35500, "ann_vol": 0.07, "spread_pips": 1.6,
        "point": 0.00001, "contract_size": 100_000, "tick_size": 0.00001,
    },
    "NZDUSD": {
        "center": 0.60500, "ann_vol": 0.08, "spread_pips": 1.8,
        "point": 0.00001, "contract_size": 100_000, "tick_size": 0.00001,
    },
}


def generate_synthetic(
    symbol: str = "EURUSD",
    n_bars: int = 5000,
    bar_seconds: float = 60.0,
    regime: str = "mixed",
    seed: int = 42,
) -> tuple[pd.DataFrame, dict]:
    """Generate realistic synthetic bid/ask bar data.

    Regimes:
      "range"   — pure mean-reversion (best case for grid)
      "trend"   — directional drift (worst case for grid)
      "mixed"   — realistic mix of ranging and trending periods
      "volatile"— high vol with occasional spikes
    """
    np.random.seed(seed)
    params = SYMBOL_PARAMS.get(symbol, SYMBOL_PARAMS["EURUSD"])
    center = params["center"]
    point = params["point"]
    spread_pips = params["spread_pips"]
    spread = spread_pips * point

    # Convert annual vol to per-bar vol
    bars_per_year = 252 * 86400 / bar_seconds
    bar_vol = params["ann_vol"] / math.sqrt(bars_per_year)

    # Generate mid price series
    mid = np.zeros(n_bars)
    mid[0] = center

    if regime == "range":
        # Ornstein-Uhlenbeck (mean-reverting)
        theta = 0.05  # mean-reversion speed
        for i in range(1, n_bars):
            mid[i] = mid[i-1] + theta * (center - mid[i-1]) + bar_vol * center * np.random.normal()

    elif regime == "trend":
        # Random walk with drift
        drift = bar_vol * center * 0.3  # 30% of vol as drift
        for i in range(1, n_bars):
            mid[i] = mid[i-1] + drift + bar_vol * center * np.random.normal()

    elif regime == "volatile":
        # Range with occasional vol spikes
        theta = 0.03
        for i in range(1, n_bars):
            spike = 3.0 if np.random.random() < 0.005 else 1.0  # 0.5% chance of 3x vol
            mid[i] = mid[i-1] + theta * (center - mid[i-1]) + spike * bar_vol * center * np.random.normal()

    else:  # "mixed" — most realistic
        # Alternate between ranging and trending regimes
        theta = 0.04
        regime_state = "range"
        regime_duration = 0
        drift = 0.0
        for i in range(1, n_bars):
            regime_duration += 1
            # Regime switching (average regime lasts ~500 bars)
            if np.random.random() < 1.0 / 500:
                if regime_state == "range":
                    regime_state = "trend"
                    drift = bar_vol * center * np.random.choice([-0.3, 0.3])
                else:
                    regime_state = "range"
                    drift = 0.0
                regime_duration = 0

            if regime_state == "range":
                mid[i] = mid[i-1] + theta * (center - mid[i-1]) + bar_vol * center * np.random.normal()
            else:
                mid[i] = mid[i-1] + drift + bar_vol * center * np.random.normal() * 1.2

    # Ensure positive
    mid = np.maximum(mid, center * 0.5)

    # Generate OHLC from mid
    bar_noise = bar_vol * center * 0.5
    high_offset = np.abs(np.random.normal(0, bar_noise, n_bars))
    low_offset = np.abs(np.random.normal(0, bar_noise, n_bars))

    mid_open = np.roll(mid, 1)
    mid_open[0] = mid[0]

    half_spread = spread / 2
    # Add some spread variation (realistic)
    spread_var = np.random.uniform(0.8, 1.3, n_bars) * half_spread

    df = pd.DataFrame({
        "bid_open": mid_open - spread_var,
        "bid_high": mid + high_offset - spread_var * 0.8,
        "bid_low": mid - low_offset - spread_var * 1.2,
        "bid_close": mid - spread_var,
        "ask_open": mid_open + spread_var,
        "ask_high": mid + high_offset + spread_var * 1.2,
        "ask_low": mid - low_offset + spread_var * 0.8,
        "ask_close": mid + spread_var,
    })
    df["mid_close"] = mid

    return df, params


def load_mt5_data(symbol: str, timeframe: str, bars: int) -> tuple[pd.DataFrame, dict]:
    """Download bid/ask data from MT5."""
    import MetaTrader5 as mt5
    from brokers.mt5 import MT5Broker, TF_MAP

    broker = MT5Broker()
    if not broker.connect():
        raise RuntimeError("Cannot connect to MT5")

    sym_info = broker.symbol_info(symbol)
    if sym_info is None:
        broker.disconnect()
        raise RuntimeError(f"Symbol {symbol} not found in MT5")

    params = {
        "center": 0,
        "point": sym_info.get("point", 0.00001),
        "contract_size": sym_info.get("trade_contract_size", 100_000),
        "tick_size": sym_info.get("trade_tick_size", sym_info.get("point", 0.00001)),
    }

    tf = TF_MAP.get(timeframe, mt5.TIMEFRAME_M1)

    # Get bid bars (default)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) == 0:
        broker.disconnect()
        raise RuntimeError(f"No data for {symbol} {timeframe}")

    df = pd.DataFrame(rates)

    # MT5 copy_rates gives us OHLCV — these are bid-side prices
    # We need to reconstruct ask from spread
    tick = broker.symbol_tick(symbol)
    current_spread = (tick["ask"] - tick["bid"]) if tick else params["point"] * 15

    df["bid_open"] = df["open"]
    df["bid_high"] = df["high"]
    df["bid_low"] = df["low"]
    df["bid_close"] = df["close"]
    df["ask_open"] = df["open"] + current_spread
    df["ask_high"] = df["high"] + current_spread
    df["ask_low"] = df["low"] + current_spread
    df["ask_close"] = df["close"] + current_spread
    df["mid_close"] = (df["bid_close"] + df["ask_close"]) / 2.0

    broker.disconnect()
    return df, params


# ═════════════════════════════════════════════════════════════════════════════
#  DISPLAY & REPORTING
# ═════════════════════════════════════════════════════════════════════════════

def print_results(result: BacktestResult, label: str = "", params: dict = None):
    """Print backtest results in a readable format."""
    print()
    print("=" * 70)
    if label:
        print(f"  {label}")
        print("=" * 70)

    starting = result.equity_curve[0] if result.equity_curve else 10000
    final = result.equity_curve[-1] if result.equity_curve else 10000
    total_return = (final / starting - 1) * 100

    print(f"  Starting Equity:   ${starting:>12,.2f}")
    print(f"  Final Equity:      ${final:>12,.2f}")
    print(f"  Total Return:       {total_return:>11.2f}%")
    print(f"  Net P&L:           ${result.total_pnl_net:>12,.2f}")
    print()
    print(f"  Trades:             {result.trades:>11d}")
    print(f"  Wins:               {result.wins:>11d}  ({result.wins/max(result.trades,1)*100:.1f}%)")
    print(f"  Losses:             {result.losses:>11d}  ({result.losses/max(result.trades,1)*100:.1f}%)")
    print()
    print(f"  Max Drawdown:       {result.max_drawdown:>11.2f}%")
    print(f"  Sharpe Ratio:       {result.sharpe:>11.2f}")
    print(f"  Sortino Ratio:      {result.sortino:>11.2f}")
    print(f"  Profit Factor:      {result.profit_factor:>11.2f}")
    print(f"  Calmar Ratio:       {result.calmar:>11.2f}")
    print(f"  CVaR (95%):         {result.cvar_95:>11.4f}%")
    print()
    print(f"  Total Commission:  ${result.total_commission:>12,.2f}")
    print(f"  Total Swap:        ${result.total_swap:>12,.2f}")
    print(f"  Max Inventory:      {result.max_inventory:>11.2f} lots")

    if result.trades > 0:
        avg_pnl = result.total_pnl_net / result.trades
        print(f"  Avg P&L / Trade:   ${avg_pnl:>12,.2f}")

    print("=" * 70)


def print_equity_chart(equity_curve: list[float], width: int = 60):
    """Print a simple ASCII equity chart."""
    if len(equity_curve) < 2:
        return

    # Sample points to fit width
    step = max(1, len(equity_curve) // width)
    sampled = equity_curve[::step]

    mn, mx = min(sampled), max(sampled)
    rng = mx - mn
    if rng < 0.01:
        rng = 1.0
    height = 15

    print(f"\n  Equity Curve ({len(equity_curve)} bars)")
    print(f"  {'':>10} {'_' * width}")

    for row in range(height, -1, -1):
        threshold = mn + (row / height) * rng
        label = f"${threshold:>9,.0f}" if row % 3 == 0 else " " * 10
        line = ""
        for val in sampled:
            if val >= threshold:
                line += "#"
            else:
                line += " "
        print(f"  {label} |{line}|")

    print(f"  {'':>10} {'_' * width}")
    print(f"  {'':>10} bar 0{' ' * (width - 10)}bar {len(equity_curve)}")


def save_results(result: BacktestResult, symbol: str, output_dir: str = "data/backtests"):
    """Save backtest results to CSV files."""
    out = Path(output_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out / f"grid_{symbol}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Equity curve
    eq_df = pd.DataFrame({"bar": range(len(result.equity_curve)), "equity": result.equity_curve})
    eq_df.to_csv(run_dir / "equity_curve.csv", index=False)

    # Trades
    if result.trade_records:
        trades_df = pd.DataFrame([
            {
                "bar": t.bar_index, "side": t.side,
                "entry": t.entry_price, "exit": t.exit_price,
                "volume": t.volume, "pnl_gross": t.pnl_gross,
                "pnl_net": t.pnl_net, "rung": t.rung_id,
            }
            for t in result.trade_records
        ])
        trades_df.to_csv(run_dir / "trades.csv", index=False)

    # Summary
    summary = {
        "symbol": symbol,
        "trades": result.trades,
        "wins": result.wins,
        "losses": result.losses,
        "total_pnl_net": round(result.total_pnl_net, 2),
        "max_drawdown": round(result.max_drawdown, 2),
        "sharpe": round(result.sharpe, 2),
        "sortino": round(result.sortino, 2),
        "profit_factor": round(result.profit_factor, 2),
        "calmar": round(result.calmar, 2),
        "total_commission": round(result.total_commission, 2),
        "total_swap": round(result.total_swap, 2),
        "max_inventory": round(result.max_inventory, 4),
    }
    import json
    with open(run_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n  Results saved to: {run_dir}")


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Grid Strategy Backtester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick synthetic test (works anytime):
  python -m backtest.run_backtest --synthetic --symbol EURUSD

  # Stress test with 2x spread:
  python -m backtest.run_backtest --synthetic --spread-mult 2.0 --slip 2

  # Different market regimes:
  python -m backtest.run_backtest --synthetic --regime range    # best case
  python -m backtest.run_backtest --synthetic --regime trend    # worst case
  python -m backtest.run_backtest --synthetic --regime mixed    # realistic
  python -m backtest.run_backtest --synthetic --regime volatile # vol spikes

  # Walk-forward validation:
  python -m backtest.run_backtest --synthetic --bars 20000 --walk-forward 5

  # From MT5 (market must be open):
  python -m backtest.run_backtest --mt5 --symbol EURUSD --timeframe M1 --bars 5000

  # Multi-symbol comparison:
  python -m backtest.run_backtest --synthetic --compare EURUSD,EURGBP,AUDNZD
""",
    )

    # Data source
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic", action="store_true", help="Use synthetic data")
    source.add_argument("--mt5", action="store_true", help="Download from MT5")
    source.add_argument("--csv", type=str, help="Load from CSV file")

    # Symbol & data params
    parser.add_argument("--symbol", type=str, default="EURUSD", help="Symbol (default: EURUSD)")
    parser.add_argument("--bars", type=int, default=5000, help="Number of bars (default: 5000)")
    parser.add_argument("--timeframe", type=str, default="M1", help="MT5 timeframe (default: M1)")
    parser.add_argument("--bar-seconds", type=float, default=60.0, help="Seconds per bar (default: 60)")
    parser.add_argument("--regime", type=str, default="mixed",
                       choices=["range", "trend", "mixed", "volatile"],
                       help="Synthetic regime (default: mixed)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")

    # Backtest params
    parser.add_argument("--equity", type=float, default=10000.0, help="Starting equity (default: 10000)")
    parser.add_argument("--spread-mult", type=float, default=1.0, help="Spread multiplier for stress test")
    parser.add_argument("--slip", type=int, default=None, help="Slippage ticks (default: from config)")
    parser.add_argument("--commission", type=float, default=None, help="Commission per lot (default: from config)")

    # Analysis
    parser.add_argument("--walk-forward", type=int, default=0, help="Walk-forward windows (0=disabled)")
    parser.add_argument("--compare", type=str, default="", help="Compare multiple symbols (comma-separated)")
    parser.add_argument("--save", action="store_true", help="Save results to data/backtests/")
    parser.add_argument("--no-chart", action="store_true", help="Skip equity chart")

    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("  GRID STRATEGY BACKTESTER")
    print("=" * 70)

    # ── Multi-symbol comparison mode ─────────────────────────────────────
    if args.compare:
        symbols = [s.strip() for s in args.compare.split(",")]
        print(f"\n  Comparing {len(symbols)} symbols: {', '.join(symbols)}")
        print(f"  Regime: {args.regime} | Bars: {args.bars} | "
              f"Spread mult: {args.spread_mult} | Slip: {args.slip or 'default'}")
        print()

        results: list[tuple[str, BacktestResult]] = []
        for sym in symbols:
            df, params = generate_synthetic(
                sym, args.bars, args.bar_seconds, args.regime, args.seed
            )
            engine = GridBacktestEngine(
                df, params["contract_size"], params["tick_size"],
                commission_per_lot=args.commission,
                bar_seconds=args.bar_seconds,
            )
            result = engine.run(
                starting_equity=args.equity,
                slip_ticks=args.slip,
                spread_mult=args.spread_mult,
            )
            results.append((sym, result))

        # Comparison table
        print(f"  {'Symbol':<10} {'Net P&L':>10} {'Trades':>7} {'Win%':>6} "
              f"{'MaxDD':>7} {'Sharpe':>7} {'PF':>6} {'Calmar':>7}")
        print("  " + "-" * 68)
        for sym, r in sorted(results, key=lambda x: x[1].total_pnl_net, reverse=True):
            wr = r.wins / max(r.trades, 1) * 100
            print(f"  {sym:<10} ${r.total_pnl_net:>9,.2f} {r.trades:>7} {wr:>5.1f}% "
                  f"{r.max_drawdown:>6.1f}% {r.sharpe:>7.2f} {r.profit_factor:>5.2f} {r.calmar:>7.2f}")
        print()
        return

    # ── Single symbol mode ───────────────────────────────────────────────
    symbol = args.symbol

    # Load data
    if args.synthetic:
        print(f"\n  Data: SYNTHETIC ({args.regime} regime)")
        print(f"  Symbol: {symbol} | Bars: {args.bars} | "
              f"Bar length: {args.bar_seconds}s | Seed: {args.seed}")
        df, params = generate_synthetic(
            symbol, args.bars, args.bar_seconds, args.regime, args.seed
        )
    elif args.mt5:
        print(f"\n  Data: MT5 LIVE")
        print(f"  Symbol: {symbol} | TF: {args.timeframe} | Bars: {args.bars}")
        df, params = load_mt5_data(symbol, args.timeframe, args.bars)
    else:
        print(f"\n  Data: CSV ({args.csv})")
        df = normalize_bars(pd.read_csv(args.csv))
        params = SYMBOL_PARAMS.get(symbol, SYMBOL_PARAMS["EURUSD"])

    print(f"  Loaded {len(df)} bars")
    print(f"  Spread mult: {args.spread_mult}x | Slip: {args.slip or 'default'} ticks")
    print(f"  Config: levels={cfg.GRID_LEVELS} anchor_hl={cfg.ANCHOR_HALFLIFE_SECONDS}s "
          f"vol_horizon={cfg.VOL_HORIZON_SECONDS}s")
    print(f"  Spacing: k_sigma={cfg.GRID_SPACING_K_SIGMA} k_cost={cfg.GRID_SPACING_K_COST}")

    # ── Walk-forward mode ────────────────────────────────────────────────
    if args.walk_forward > 0:
        print(f"\n  Running walk-forward validation with {args.walk_forward} windows ...")
        windows = walk_forward(
            df,
            contract_size=params["contract_size"],
            tick_size=params["tick_size"],
            n_windows=args.walk_forward,
            starting_equity=args.equity,
            slip_ticks=args.slip,
            spread_mult=args.spread_mult,
            bar_seconds=args.bar_seconds,
        )

        print(f"\n  {'Window':<8} {'Train P&L':>10} {'Test P&L':>10} "
              f"{'Train DD':>9} {'Test DD':>8} {'Test Sharpe':>12} {'Test Trades':>12}")
        print("  " + "-" * 72)

        oos_pnls = []
        for i, w in enumerate(windows):
            t_pnl = w.train_result.total_pnl_net if w.train_result else 0
            t_dd = w.train_result.max_drawdown if w.train_result else 0
            o_pnl = w.test_result.total_pnl_net if w.test_result else 0
            o_dd = w.test_result.max_drawdown if w.test_result else 0
            o_sh = w.test_result.sharpe if w.test_result else 0
            o_tr = w.test_result.trades if w.test_result else 0
            oos_pnls.append(o_pnl)
            print(f"  {i+1:<8} ${t_pnl:>9,.2f} ${o_pnl:>9,.2f} "
                  f"{t_dd:>8.1f}% {o_dd:>7.1f}% {o_sh:>11.2f} {o_tr:>12d}")

        avg_oos = sum(oos_pnls) / len(oos_pnls) if oos_pnls else 0
        pos_windows = sum(1 for p in oos_pnls if p > 0)
        print(f"\n  Walk-forward summary:")
        print(f"    Avg OOS P&L:      ${avg_oos:>10,.2f}")
        print(f"    Positive windows:  {pos_windows}/{len(windows)}")
        print(f"    Consistency:       {pos_windows/max(len(windows),1)*100:.0f}%")
        return

    # ── Standard backtest ────────────────────────────────────────────────
    engine = GridBacktestEngine(
        df,
        contract_size=params["contract_size"],
        tick_size=params["tick_size"],
        commission_per_lot=args.commission,
        bar_seconds=args.bar_seconds,
    )
    result = engine.run(
        starting_equity=args.equity,
        slip_ticks=args.slip,
        spread_mult=args.spread_mult,
    )

    print_results(result, f"GRID BACKTEST: {symbol} ({args.regime if args.synthetic else 'live'})")

    if not args.no_chart:
        print_equity_chart(result.equity_curve)

    if args.save:
        save_results(result, symbol)

    # Quick regime comparison hint
    if args.synthetic and args.regime == "mixed":
        print("\n  Tip: Try different regimes to stress-test:")
        print("    --regime range     (best case: pure oscillation)")
        print("    --regime trend     (worst case: directional drift)")
        print("    --regime volatile  (vol spikes)")
        print("    --regime mixed     (realistic blend)")


if __name__ == "__main__":
    main()
