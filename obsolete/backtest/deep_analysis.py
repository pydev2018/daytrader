"""
Deep historical analysis of EURCAD:
1. Download maximum available history from MT5
2. Analyze mean-reversion properties across different time windows
3. Test grid strategy across multiple historical periods
4. Answer: has EURCAD always been grid-friendly, or only recently?
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BT_COMMISSION_PER_LOT'] = '0'
os.environ['RUNG_STOP_LOSS_SPACINGS'] = '0'
os.environ['BASE_ORDER_SIZE_LOTS'] = '0.08'
os.environ['GRID_LEVELS'] = '6'

import math
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from brokers.mt5 import MT5Broker, TF_MAP
from backtest.grid_engine import GridBacktestEngine
from grid.scanner import _hurst_exponent, _variance_ratio
import importlib
import config.settings
importlib.reload(config.settings)

broker = MT5Broker()
if not broker.connect():
    print("Cannot connect"); sys.exit(1)

mt5.symbol_select("EURCAD", True)
sym_info = mt5.symbol_info("EURCAD")
point = sym_info.point
contract_size = sym_info.trade_contract_size
tick_size = sym_info.trade_tick_size or point

# ── Download maximum history at multiple timeframes ──────────────────
print("Downloading maximum EURCAD history from MT5...")
print()

data = {}
for tf_name, tf_const, bar_secs in [
    ("M5", mt5.TIMEFRAME_M5, 300),
    ("H1", mt5.TIMEFRAME_H1, 3600),
    ("D1", mt5.TIMEFRAME_D1, 86400),
]:
    rates = mt5.copy_rates_from_pos("EURCAD", tf_const, 0, 50000)
    if rates is not None and len(rates) > 0:
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        data[tf_name] = df
        days = (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds() / 86400
        print("  %s: %d bars (%.0f days) from %s to %s" % (
            tf_name, len(df), days,
            df["time"].iloc[0].strftime("%Y-%m-%d"),
            df["time"].iloc[-1].strftime("%Y-%m-%d")))

broker.disconnect()

# ═════════════════════════════════════════════════════════════════════════
#  PART 1: LONG-TERM PRICE BEHAVIOR (Daily bars)
# ═════════════════════════════════════════════════════════════════════════
print()
print("=" * 85)
print("  PART 1: EURCAD LONG-TERM PRICE BEHAVIOR")
print("=" * 85)

if "D1" in data:
    d1 = data["D1"]
    close = d1["close"].values
    
    print()
    print("  Full history: %s to %s (%.1f years)" % (
        d1["time"].iloc[0].strftime("%Y-%m-%d"),
        d1["time"].iloc[-1].strftime("%Y-%m-%d"),
        len(d1) / 252))
    print("  Price range:  %.5f to %.5f" % (np.min(close), np.max(close)))
    print("  Mean price:   %.5f" % np.mean(close))
    print("  Current:      %.5f" % close[-1])
    total_range = np.max(close) - np.min(close)
    print("  Total range:  %.0f pips" % (total_range / point))
    
    # Annual statistics
    print()
    print("  Year-by-Year Analysis:")
    print("  %s  %8s  %8s  %8s  %6s  %6s" % ("Year", "Open", "Close", "Range", "Hurst", "VR"))
    print("  " + "-" * 55)
    
    years = sorted(d1["time"].dt.year.unique())
    for year in years:
        mask = d1["time"].dt.year == year
        yr = d1[mask]
        if len(yr) < 20:
            continue
        c = yr["close"].values
        h = yr["high"].values
        l = yr["low"].values
        yr_range = (np.max(h) - np.min(l)) / point
        log_ret = np.diff(np.log(c))
        hurst = _hurst_exponent(c)
        vr = _variance_ratio(log_ret, lag=5)
        
        verdict = ""
        if hurst < 0.50:
            verdict = "MEAN-REV"
        elif hurst < 0.55:
            verdict = "neutral"
        else:
            verdict = "trending"
        
        print("  %d  %8.5f  %8.5f  %6.0f p  %5.3f  %5.3f  %s" % (
            year, c[0], c[-1], yr_range, hurst, vr, verdict))

# ═════════════════════════════════════════════════════════════════════════
#  PART 2: MEAN-REVERSION STABILITY (H1 bars, rolling windows)
# ═════════════════════════════════════════════════════════════════════════
print()
print("=" * 85)
print("  PART 2: MEAN-REVERSION STABILITY OVER TIME (H1 bars)")
print("=" * 85)

if "H1" in data:
    h1 = data["H1"]
    close_h1 = h1["close"].values
    times_h1 = h1["time"].values
    
    # Rolling Hurst and VR with 500-bar windows (~21 days)
    window = 500
    hurst_series = []
    vr_series = []
    dates = []
    
    for i in range(window, len(close_h1), 50):  # step by 50 bars (~2 days)
        chunk = close_h1[i-window:i]
        log_ret = np.diff(np.log(chunk))
        h = _hurst_exponent(chunk)
        v = _variance_ratio(log_ret, lag=5)
        hurst_series.append(h)
        vr_series.append(v)
        dates.append(pd.Timestamp(times_h1[i]))
    
    hurst_arr = np.array(hurst_series)
    vr_arr = np.array(vr_series)
    
    print()
    print("  Rolling 500-bar (21-day) Hurst exponent:")
    print("    Mean:    %.4f" % np.mean(hurst_arr))
    print("    Median:  %.4f" % np.median(hurst_arr))
    print("    Min:     %.4f" % np.min(hurst_arr))
    print("    Max:     %.4f" % np.max(hurst_arr))
    print("    Std:     %.4f" % np.std(hurst_arr))
    pct_mr = np.sum(hurst_arr < 0.50) / len(hurst_arr) * 100
    pct_neutral = np.sum((hurst_arr >= 0.50) & (hurst_arr < 0.55)) / len(hurst_arr) * 100
    pct_trend = np.sum(hurst_arr >= 0.55) / len(hurst_arr) * 100
    print("    Mean-reverting (H<0.50): %.1f%% of the time" % pct_mr)
    print("    Neutral (0.50-0.55):     %.1f%% of the time" % pct_neutral)
    print("    Trending (H>0.55):       %.1f%% of the time" % pct_trend)
    
    print()
    print("  Rolling Variance Ratio:")
    print("    Mean:    %.4f" % np.mean(vr_arr))
    pct_vr_mr = np.sum(vr_arr < 0.90) / len(vr_arr) * 100
    print("    VR < 0.90 (mean-reverting): %.1f%% of the time" % pct_vr_mr)

    # Show Hurst by quarter
    print()
    print("  Hurst by Quarter:")
    print("  %10s  %6s  %8s" % ("Quarter", "Hurst", "Regime"))
    print("  " + "-" * 30)
    
    for date, h in zip(dates, hurst_series):
        q = "%d-Q%d" % (date.year, (date.month - 1) // 3 + 1)
        pass  # We'll aggregate below
    
    # Aggregate by quarter
    q_map = {}
    for date, h in zip(dates, hurst_series):
        q = "%d-Q%d" % (date.year, (date.month - 1) // 3 + 1)
        if q not in q_map:
            q_map[q] = []
        q_map[q].append(h)
    
    for q in sorted(q_map.keys()):
        vals = q_map[q]
        avg_h = np.mean(vals)
        regime = "MEAN-REV" if avg_h < 0.50 else ("neutral" if avg_h < 0.55 else "trending")
        bar = "#" * int(avg_h * 40)
        print("  %10s  %5.3f  %-10s %s" % (q, avg_h, regime, bar))

# ═════════════════════════════════════════════════════════════════════════
#  PART 3: GRID BACKTEST ACROSS DIFFERENT PERIODS (M5 bars)
# ═════════════════════════════════════════════════════════════════════════
print()
print("=" * 85)
print("  PART 3: GRID STRATEGY PERFORMANCE ACROSS DIFFERENT PERIODS")
print("  0.08 lots, $1,000 capital, 6 levels, real data")
print("=" * 85)

if "M5" in data:
    m5 = data["M5"]
    
    # Add bid/ask columns from spread
    spread_price = m5["spread"].values * point
    m5_bt = m5.copy()
    m5_bt["bid_open"] = m5_bt["open"]
    m5_bt["bid_high"] = m5_bt["high"]
    m5_bt["bid_low"] = m5_bt["low"]
    m5_bt["bid_close"] = m5_bt["close"]
    m5_bt["ask_open"] = m5_bt["open"] + spread_price
    m5_bt["ask_high"] = m5_bt["high"] + spread_price
    m5_bt["ask_low"] = m5_bt["low"] + spread_price
    m5_bt["ask_close"] = m5_bt["close"] + spread_price
    m5_bt["mid_close"] = (m5_bt["bid_close"] + m5_bt["ask_close"]) / 2.0
    
    # Split into monthly chunks and backtest each
    m5_bt["month"] = m5_bt["time"].dt.to_period("M")
    months = sorted(m5_bt["month"].unique())
    
    print()
    print("  %10s  %8s  %8s  %6s  %6s  %6s  %8s" % (
        "Month", "Net PnL", "Realized", "Trades", "MaxDD", "MaxInv", "Verdict"))
    print("  " + "-" * 65)
    
    monthly_pnls = []
    monthly_dds = []
    
    for month in months:
        chunk = m5_bt[m5_bt["month"] == month].reset_index(drop=True)
        if len(chunk) < 200:
            continue
        
        engine = GridBacktestEngine(
            chunk, contract_size, tick_size,
            commission_per_lot=0.0, bar_seconds=300.0,
        )
        r = engine.run(starting_equity=1000.0, slip_ticks=1)
        
        verdict = ""
        if r.total_pnl_net > 50:
            verdict = "PROFIT"
        elif r.total_pnl_net > 0:
            verdict = "small+"
        elif r.total_pnl_net > -50:
            verdict = "small-"
        else:
            verdict = "LOSS"
        
        monthly_pnls.append(r.total_pnl_net)
        monthly_dds.append(r.max_drawdown)
        
        print("  %10s  $%+7.2f  $%+7.2f  %5d  %5.1f%%  %5.3f  %s" % (
            str(month), r.total_pnl_net, r.total_pnl, r.trades,
            r.max_drawdown, r.max_inventory, verdict))
    
    if monthly_pnls:
        print("  " + "-" * 65)
        pos_months = sum(1 for p in monthly_pnls if p > 0)
        neg_months = sum(1 for p in monthly_pnls if p <= 0)
        avg_pnl = np.mean(monthly_pnls)
        avg_dd = np.mean(monthly_dds)
        worst_month = min(monthly_pnls)
        best_month = max(monthly_pnls)
        total = sum(monthly_pnls)
        
        print()
        print("  MONTHLY SUMMARY:")
        print("    Total months tested:   %d" % len(monthly_pnls))
        print("    Profitable months:     %d (%.0f%%)" % (pos_months, pos_months/len(monthly_pnls)*100))
        print("    Losing months:         %d (%.0f%%)" % (neg_months, neg_months/len(monthly_pnls)*100))
        print("    Average monthly PnL:   $%+.2f" % avg_pnl)
        print("    Best month:            $%+.2f" % best_month)
        print("    Worst month:           $%+.2f" % worst_month)
        print("    Total PnL:             $%+.2f" % total)
        print("    Average max DD:        %.1f%%" % avg_dd)
        print("    Worst max DD:          %.1f%%" % max(monthly_dds))
        
        # Consistency score
        if pos_months > 0:
            consistency = pos_months / len(monthly_pnls) * 100
            edge = avg_pnl / max(abs(worst_month), 1)
            print()
            if consistency >= 70 and avg_pnl > 0:
                print("  VERDICT: EURCAD is a RELIABLE grid symbol")
                print("    %.0f%% of months profitable, average $%.0f/month" % (consistency, avg_pnl))
            elif consistency >= 50 and avg_pnl > 0:
                print("  VERDICT: EURCAD is DECENT but not bulletproof")
                print("    %.0f%% of months profitable" % consistency)
            else:
                print("  VERDICT: EURCAD may not be reliable enough")
                print("    Only %.0f%% of months profitable" % consistency)

print()
print("=" * 85)
