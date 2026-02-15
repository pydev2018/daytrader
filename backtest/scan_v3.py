"""
Scanner v3: Normalize risk across all pairs including JPY.
Instead of using fixed 0.10 lots for everything, calculate the lot size
that gives equal dollar risk per pip across all symbols.

This way we can fairly compare EURCAD vs USDJPY vs GBPJPY.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BT_COMMISSION_PER_LOT'] = '0'
os.environ['RUNG_STOP_LOSS_SPACINGS'] = '0'

import math
import MetaTrader5 as mt5
from brokers.mt5 import MT5Broker
from grid.scanner import GridScanner, FX_UNIVERSE
from backtest.real_data_backtest import download_data, resolve_symbol
from backtest.grid_engine import GridBacktestEngine
from config import settings as cfg

broker = MT5Broker()
if not broker.connect():
    print("Cannot connect"); sys.exit(1)

# Get all symbols with data
available = []
sym_params = {}
for base in FX_UNIVERSE:
    real = resolve_symbol(base)
    info = mt5.symbol_info(real)
    if info is not None:
        if not info.visible:
            mt5.symbol_select(real, True)
        rates = mt5.copy_rates_from_pos(real, mt5.TIMEFRAME_M5, 0, 10)
        if rates is not None and len(rates) > 0:
            available.append(real)
            # Store symbol properties for lot sizing
            sym_params[real] = {
                "point": info.point,
                "digits": info.digits,
                "contract_size": info.trade_contract_size,
                "tick_size": info.trade_tick_size or info.point,
                "tick_value": info.trade_tick_value if hasattr(info, 'trade_tick_value') else 0,
            }

print(f"Found {len(available)} symbols\n")

# Calculate normalized lot size per symbol
# Target: $1 risk per pip (10 points for 5-digit, 1 point for 3-digit)
TARGET_DOLLAR_PER_PIP = 1.0  # $1 per pip movement

print("=" * 100)
print("  RISK-NORMALIZED BACKTEST: Equal $ risk per pip across ALL pairs")
print("  Target: $1.00 per pip | Starting capital: $1,000 | 100 days real data")
print("=" * 100)

# Download all data first
print("\nDownloading data...")
data_cache = {}
for real in available:
    df, params = download_data(broker, real, timeframe="M5", bars=20000)
    if df is not None:
        data_cache[real] = (df, params)

broker.disconnect()
print(f"Downloaded {len(data_cache)} symbols. Running backtests...\n")

results = []
for real, (df, params) in data_cache.items():
    point = params["point"]
    contract_size = params["contract_size"]
    digits = params.get("digits", 5)
    
    # Calculate lot size for $1/pip
    # For 5-digit: 1 pip = 10 points. pip_value per lot = 10 * point * contract_size
    # For 3-digit: 1 pip = 10 points. pip_value per lot = 10 * point * contract_size  
    # But for JPY pairs, the base currency affects pip value
    # Simplification: pip_value per standard lot ≈ $10 for USD-quoted, varies for others
    # More accurate: use the spread in dollar terms
    
    # Get typical mid price to estimate pip value
    mid_price = df["mid_close"].iloc[-1]
    
    # For pairs quoted in USD (EURUSD, GBPUSD, AUDUSD, NZDUSD):
    #   1 pip on 1.0 lot = $10
    # For pairs quoted in other currencies:
    #   1 pip on 1.0 lot = 10 * point * contract_size / exchange_rate
    # We approximate by using: pip_value ≈ contract_size * 10 * point
    # For USDJPY: 100000 * 10 * 0.001 = $1000 per lot per pip in JPY
    #   In USD: $1000 / 150 ≈ $6.67 per pip per lot
    # For EURUSD: 100000 * 10 * 0.00001 = $10 per pip per lot in USD
    
    pip_size = 10 * point  # 1 pip in price units
    
    # Estimate pip value in account currency (USD)
    # If symbol ends in USD → pip_value = contract_size * pip_size = $10/lot
    # If symbol ends in JPY → pip_value = contract_size * pip_size / JPY_rate ≈ $6.67/lot
    # If symbol ends in CHF, CAD, etc → approximate
    sym_upper = real.upper().replace(".SML", "")
    quote_ccy = sym_upper[-3:]
    
    if quote_ccy == "USD":
        pip_value_per_lot = contract_size * pip_size  # exactly $10
    elif quote_ccy == "JPY":
        pip_value_per_lot = contract_size * pip_size / mid_price  # ~$6.67
    elif quote_ccy == "CAD":
        pip_value_per_lot = contract_size * pip_size / 1.36  # approx USDCAD
    elif quote_ccy == "CHF":
        pip_value_per_lot = contract_size * pip_size / 0.90  # approx USDCHF
    elif quote_ccy == "GBP":
        pip_value_per_lot = contract_size * pip_size * 1.26  # approx GBPUSD
    elif quote_ccy == "AUD":
        pip_value_per_lot = contract_size * pip_size * 0.65  # approx AUDUSD
    elif quote_ccy == "NZD":
        pip_value_per_lot = contract_size * pip_size * 0.60  # approx NZDUSD
    else:
        pip_value_per_lot = contract_size * pip_size  # fallback
    
    # Lot size for target $/pip
    normalized_lots = TARGET_DOLLAR_PER_PIP / max(pip_value_per_lot, 0.01)
    # Round to 0.01 (min lot)
    normalized_lots = max(0.01, round(normalized_lots / 0.01) * 0.01)
    
    # Override BASE_ORDER_SIZE_LOTS for this backtest
    os.environ['BASE_ORDER_SIZE_LOTS'] = str(normalized_lots)
    # Reimport to pick up the change (hacky but works for backtest)
    import importlib
    import config.settings
    importlib.reload(config.settings)
    
    engine = GridBacktestEngine(
        df, contract_size, params["tick_size"],
        commission_per_lot=0.0, bar_seconds=300.0,
    )
    bt = engine.run(starting_equity=1000.0, slip_ticks=1)
    
    calmar = bt.total_pnl_net / max(bt.max_drawdown, 0.1) if bt.max_drawdown > 0 else 0
    
    results.append({
        "symbol": real,
        "lots": normalized_lots,
        "pip_val": pip_value_per_lot,
        "pnl": bt.total_pnl_net,
        "dd": bt.max_drawdown,
        "trades": bt.trades,
        "calmar": calmar,
        "realized": bt.total_pnl,
    })

# Sort by Calmar (risk-adjusted return)
results.sort(key=lambda x: x["calmar"], reverse=True)

print(f"  {'#':>3} {'Symbol':<14} {'Lots':>6} {'$/pip':>6} {'Net PnL':>10} "
      f"{'MaxDD':>7} {'Trades':>7} {'Calmar':>8} {'Realized':>10}")
print("  " + "-" * 85)

for i, r in enumerate(results, 1):
    marker = " <<<" if r["calmar"] > 5 and r["pnl"] > 50 else ""
    print(f"  {i:>3} {r['symbol']:<14} {r['lots']:>6.2f} {r['pip_val']:>5.1f} "
          f"${r['pnl']:>+9.2f} {r['dd']:>6.1f}% {r['trades']:>7} "
          f"{r['calmar']:>8.1f} ${r['realized']:>+9.2f}{marker}")

# Top pick
profitable = [r for r in results if r["pnl"] > 0 and r["dd"] < 50]
if profitable:
    best = profitable[0]
    print(f"\n  RECOMMENDATION FOR $1,000 ACCOUNT:")
    print(f"    Symbol:    {best['symbol']}")
    print(f"    Lot size:  {best['lots']} lots")
    print(f"    Expected:  ${best['pnl']:+.2f} over 100 days ({best['pnl']/1000*100:+.1f}%)")
    print(f"    Max DD:    {best['dd']:.1f}%")
    print(f"    Calmar:    {best['calmar']:.1f}")
    
    per_month = best["pnl"] / 100 * 22
    print(f"    Monthly:   ~${per_month:.0f} ({per_month/1000*100:.1f}%)")
    if per_month > 0:
        print(f"    Time to $2k: {1000/per_month:.1f} months")
