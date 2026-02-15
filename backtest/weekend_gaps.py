"""
Analyze all weekend gaps in EURCAD history.
Uses D1 (daily) bars to find Friday close → Monday open gaps.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from brokers.mt5 import MT5Broker
from config import settings as cfg

broker = MT5Broker()
if not broker.connect():
    print("Cannot connect"); sys.exit(1)

mt5.symbol_select("EURCAD", True)

# Get maximum daily history
rates = mt5.copy_rates_from_pos("EURCAD", mt5.TIMEFRAME_D1, 0, 50000)
broker.disconnect()

if rates is None:
    print("No data"); sys.exit(1)

df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
df["weekday"] = df["time"].dt.weekday  # 0=Mon, 4=Fri

point = 0.00001

print("Downloaded %d daily bars: %s to %s" % (
    len(df), df["time"].iloc[0].strftime("%Y-%m-%d"),
    df["time"].iloc[-1].strftime("%Y-%m-%d")))

# Find weekend gaps: Friday close → Monday open
gaps = []
for i in range(1, len(df)):
    prev = df.iloc[i - 1]
    curr = df.iloc[i]

    # Look for Monday bars preceded by Thursday/Friday bars
    # (some data may skip days)
    days_between = (curr["time"] - prev["time"]).days
    if days_between >= 2:  # at least a weekend gap
        friday_close = prev["close"]
        monday_open = curr["open"]
        gap_pips = abs(monday_open - friday_close) / point
        direction = "UP" if monday_open > friday_close else "DOWN"
        gaps.append({
            "date": curr["time"].strftime("%Y-%m-%d"),
            "friday_close": friday_close,
            "monday_open": monday_open,
            "gap_pips": gap_pips,
            "direction": direction,
            "days_gap": days_between,
        })

gaps_df = pd.DataFrame(gaps)

print()
print("=" * 75)
print("  EURCAD WEEKEND GAP ANALYSIS")
print("  %d weekends analyzed (%s to %s)" % (
    len(gaps),
    gaps_df["date"].iloc[0] if len(gaps) > 0 else "N/A",
    gaps_df["date"].iloc[-1] if len(gaps) > 0 else "N/A"))
print("=" * 75)

if len(gaps) == 0:
    print("  No gaps found")
    sys.exit(0)

gap_pips = gaps_df["gap_pips"].values

print()
print("  STATISTICS:")
print("    Total weekends:    %d" % len(gaps))
print("    Mean gap:          %.1f pips" % np.mean(gap_pips))
print("    Median gap:        %.1f pips" % np.median(gap_pips))
print("    Std deviation:     %.1f pips" % np.std(gap_pips))
print("    Max gap:           %.1f pips" % np.max(gap_pips))
print("    Min gap:           %.1f pips" % np.min(gap_pips))

print()
print("  DISTRIBUTION:")
thresholds = [10, 20, 30, 50, 75, 100, 150, 200, 300, 500]
for t in thresholds:
    count = np.sum(gap_pips >= t)
    pct = count / len(gaps) * 100
    bar = "#" * int(pct)
    print("    >= %3d pips:  %4d (%5.1f%%)  %s" % (t, count, pct, bar))

# Show the 20 largest gaps ever
print()
print("  TOP 20 LARGEST WEEKEND GAPS:")
print("  %12s  %10s  %10s  %8s  %5s" % ("Date", "Fri Close", "Mon Open", "Gap", "Dir"))
print("  " + "-" * 50)

top = gaps_df.nlargest(20, "gap_pips")
for _, row in top.iterrows():
    print("  %12s  %10.5f  %10.5f  %6.0f p  %s" % (
        row["date"], row["friday_close"], row["monday_open"],
        row["gap_pips"], row["direction"]))

# Answer the key question
print()
print("  KEY QUESTION: Has a 200+ pip weekend gap ever happened?")
big_gaps = gaps_df[gaps_df["gap_pips"] >= 200]
if len(big_gaps) == 0:
    print("    NO — never in %d weekends (%.1f years)" % (len(gaps), len(gaps)/52))
else:
    print("    YES — %d times:" % len(big_gaps))
    for _, row in big_gaps.iterrows():
        print("      %s: %.0f pips %s" % (row["date"], row["gap_pips"], row["direction"]))

# What about 100+ pip gaps?
print()
print("  Has a 100+ pip weekend gap ever happened?")
big100 = gaps_df[gaps_df["gap_pips"] >= 100]
if len(big100) == 0:
    print("    NO — never in %d weekends" % len(gaps))
else:
    print("    YES — %d times (%.1f%% of weekends):" % (len(big100), len(big100)/len(gaps)*100))
    for _, row in big100.iterrows():
        print("      %s: %.0f pips %s (Fri=%.5f Mon=%.5f)" % (
            row["date"], row["gap_pips"], row["direction"],
            row["friday_close"], row["monday_open"]))

# Impact analysis at our lot size
print()
print("  IMPACT ON $1,500 ACCOUNT (0.08 lots, worst case 6 positions):")
max_gap = np.max(gap_pips)
for n_pos in [1, 3, 6]:
    loss = max_gap * point * n_pos * 0.08 * 100000
    pct = loss / 1500 * 100
    print("    %d positions x %.0f pip max gap = $%.2f loss (%.1f%%)" % (
        n_pos, max_gap, loss, pct))
