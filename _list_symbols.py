import MetaTrader5 as mt5
mt5.initialize()
syms = mt5.symbols_get()
print(f"Total symbols: {len(syms)}")
visible = [s for s in syms if s.visible]
print(f"Visible: {len(visible)}")
tradeable = [s for s in syms if s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL]
print(f"Tradeable (FULL): {len(tradeable)}")
print()
for s in sorted(syms, key=lambda x: x.name):
    v = "V" if s.visible else " "
    t = "T" if s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL else " "
    print(f"  [{v}{t}] {s.name:30s} path={s.path}")
mt5.shutdown()
