"""
Check what FX symbols are actually available in this MT5 terminal.
OANDA uses suffixes like .sml, .pro etc. This script discovers the real names.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from config import settings as cfg

kwargs = {}
if cfg.MT5_PATH:
    kwargs["path"] = cfg.MT5_PATH
if cfg.MT5_LOGIN:
    kwargs["login"] = cfg.MT5_LOGIN
if cfg.MT5_PASSWORD:
    kwargs["password"] = cfg.MT5_PASSWORD
if cfg.MT5_SERVER:
    kwargs["server"] = cfg.MT5_SERVER
kwargs["timeout"] = cfg.MT5_TIMEOUT

print("Connecting to MT5...")
if not mt5.initialize(**kwargs):
    print(f"MT5 initialize failed: {mt5.last_error()}")
    sys.exit(1)

info = mt5.terminal_info()
acc = mt5.account_info()
print(f"Connected: terminal={info.name} account={acc.login} server={acc.server}")

# Get ALL symbols from the terminal
all_symbols = mt5.symbols_get()
if all_symbols is None:
    print("Cannot retrieve symbols list")
    mt5.shutdown()
    sys.exit(1)

print(f"\nTotal symbols in terminal: {len(all_symbols)}")

# Find all FX-related symbols
BASE_PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD",
    "CADJPY", "CADCHF", "CHFJPY",
]

print(f"\n{'='*80}")
print("  SYMBOL NAME DISCOVERY")
print(f"{'='*80}")
print(f"\n  Looking for FX pairs (OANDA may use suffixes like .sml, .pro, etc.)\n")

# Build a lookup: for each base pair, find matching real symbol names
found_map = {}  # base_pair -> real_symbol_name

for base in BASE_PAIRS:
    matches = []
    for sym in all_symbols:
        name = sym.name.upper()
        # Check if the symbol name starts with the base pair
        if name.startswith(base):
            # Verify it's tradeable
            can_trade = sym.trade_mode > 0
            has_data = False
            # Try to get data
            mt5.symbol_select(sym.name, True)
            rates = mt5.copy_rates_from_pos(sym.name, mt5.TIMEFRAME_M5, 0, 10)
            if rates is not None and len(rates) > 0:
                has_data = True
            matches.append({
                "name": sym.name,
                "path": sym.path,
                "description": sym.description if hasattr(sym, 'description') else "",
                "digits": sym.digits,
                "point": sym.point,
                "spread": sym.spread,
                "trade_mode": sym.trade_mode,
                "can_trade": can_trade,
                "has_data": has_data,
                "contract_size": sym.trade_contract_size,
                "volume_min": sym.volume_min,
            })

    if matches:
        # Prefer the one with data, then the shortest name
        with_data = [m for m in matches if m["has_data"]]
        best = with_data[0] if with_data else matches[0]
        found_map[base] = best["name"]
        
        status = "OK" if best["has_data"] else "NO DATA"
        suffix = best["name"].replace(base, "") if base in best["name"].upper() else ""
        print(f"  {base:<10} -> {best['name']:<18} [{status}]  "
              f"digits={best['digits']}  spread={best['spread']}  "
              f"lot_min={best['volume_min']}  contract={best['contract_size']}")
        
        # Show all variants if multiple
        if len(matches) > 1:
            for m in matches:
                if m["name"] != best["name"]:
                    s = "OK" if m["has_data"] else "NO DATA"
                    print(f"{'':>14} also: {m['name']:<18} [{s}]")
    else:
        print(f"  {base:<10} -> NOT FOUND in terminal")

# Summary
print(f"\n{'='*80}")
print("  SUMMARY")
print(f"{'='*80}")
data_ok = [base for base, name in found_map.items() 
           if any(m["has_data"] for m in [{"has_data": True}])]  # simplified

print(f"\n  Found {len(found_map)}/{len(BASE_PAIRS)} base pairs")
print(f"\n  Symbol mapping (copy this to use in backtest):")
print(f"  SYMBOL_MAP = {{")
for base, real in sorted(found_map.items()):
    print(f'      "{base}": "{real}",')
print(f"  }}")

# Also check what suffix pattern OANDA uses
suffixes = set()
for base, real in found_map.items():
    suffix = real.upper().replace(base, "")
    if suffix:
        suffixes.add(suffix)
if suffixes:
    print(f"\n  Detected OANDA suffix(es): {suffixes}")
    common_suffix = max(suffixes, key=lambda s: sum(1 for r in found_map.values() if s in r.upper()))
    print(f"  Most common suffix: '{common_suffix}'")
else:
    print(f"\n  No suffix detected (symbols use standard names)")

# Deep data check on a few symbols
print(f"\n{'='*80}")
print("  DATA DEPTH CHECK (how much history is available?)")
print(f"{'='*80}\n")

test_syms = list(found_map.values())[:5]
for sym in test_syms:
    for tf_name, tf, bar_secs in [("M1", mt5.TIMEFRAME_M1, 60), 
                                    ("M5", mt5.TIMEFRAME_M5, 300),
                                    ("H1", mt5.TIMEFRAME_H1, 3600)]:
        rates = mt5.copy_rates_from_pos(sym, tf, 0, 50000)
        if rates is not None and len(rates) > 0:
            import pandas as pd
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            days = (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds() / 86400
            print(f"  {sym:<18} {tf_name}: {len(rates):>6} bars  "
                  f"({days:.0f} days)  from {df['time'].iloc[0].strftime('%Y-%m-%d')} "
                  f"to {df['time'].iloc[-1].strftime('%Y-%m-%d')}")
        else:
            print(f"  {sym:<18} {tf_name}: NO DATA")

mt5.shutdown()
print(f"\nDone.")
