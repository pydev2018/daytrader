"""Check EURCAD trading constraints and lot size validity."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from config import settings as cfg

kwargs = {}
if cfg.MT5_PATH: kwargs["path"] = cfg.MT5_PATH
if cfg.MT5_LOGIN: kwargs["login"] = cfg.MT5_LOGIN
if cfg.MT5_PASSWORD: kwargs["password"] = cfg.MT5_PASSWORD
if cfg.MT5_SERVER: kwargs["server"] = cfg.MT5_SERVER
kwargs["timeout"] = cfg.MT5_TIMEOUT

mt5.initialize(**kwargs)
info = mt5.symbol_info("EURCAD")
if not info:
    print("EURCAD not found!")
    mt5.shutdown()
    sys.exit(1)

print("EURCAD - Full Trading Constraints")
print("=" * 50)
print("  Volume minimum:   %s" % info.volume_min)
print("  Volume maximum:   %s" % info.volume_max)
print("  Volume step:      %s" % info.volume_step)
print("  Contract size:    %s" % info.trade_contract_size)
print("  Digits:           %s" % info.digits)
print("  Point:            %s" % info.point)
print("  Tick size:        %s" % info.trade_tick_size)
print("  Tick value:       %s" % info.trade_tick_value)
print("  Stops level:      %s points" % info.trade_stops_level)
print("  Freeze level:     %s points" % info.trade_freeze_level)
print("  Spread (now):     %s points" % info.spread)
print("  Swap long:        %s" % info.swap_long)
print("  Swap short:       %s" % info.swap_short)
print("  Trade mode:       %s" % info.trade_mode)
print("  Filling mode:     %s" % info.filling_mode)

vol_min = info.volume_min
vol_max = info.volume_max
vol_step = info.volume_step

print()
print("  Lot size validity check:")
print("  Min lot: %s | Max lot: %s | Step: %s" % (vol_min, vol_max, vol_step))
print()

for lot in [0.01, 0.02, 0.05, 0.08, 0.10, 0.12, 0.14, 0.20]:
    valid = lot >= vol_min and lot <= vol_max
    on_step = abs(round(lot / vol_step) * vol_step - lot) < 1e-9
    if valid and on_step:
        status = "OK"
    else:
        reasons = []
        if lot < vol_min:
            reasons.append("below min")
        if lot > vol_max:
            reasons.append("above max")
        if not on_step:
            reasons.append("not on step (%.4f)" % vol_step)
        status = "INVALID: " + ", ".join(reasons)
    print("    %.2f lots: %s" % (lot, status))

# Margin check for 0.08 lots
print()
print("  Margin requirements (0.08 lots):")
m = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, "EURCAD", 0.08, 1.50)
if m is not None:
    print("    Single position: $%.2f" % m)
    print("    6 positions:     $%.2f" % (m * 6))
    print("    On $1000 account: margin level = %.0f%%" % (1000 / (m * 6) * 100))

mt5.shutdown()
