# OCO Breakout Trading System (MT5 Python)

Pure OCO breakout algorithmic trading runtime for MetaTrader 5 (OANDA MT5). The live engine places paired stop-entry orders above/below breakout levels, cancels the opposite side after trigger, and manages the open leg with SL, trailing, time-stop, cooldown, and account-level risk halts.

## What It Does
- Connects to MT5 and runs OCO breakout logic on configured symbols (`oco/engine.py`).
- Arms paired stop orders from lookback breakout levels with spread/ATR buffer.
- Cancels stale/opposite pending legs and keeps at most one live OCO position per symbol.
- Applies SL, trailing SL, and time-stop lifecycle management on the triggered leg.
- Enforces account risk limits and unwind behavior (`risk/manager.py`).

## Quickstart (Windows)
1. Install dependencies: `pip install -r requirements.txt`
2. Configure `.env`
3. Run:
   - Live: `python main.py`
   - Status: `python main.py --status`

## Configuration
Configuration lives in `config/settings.py` and `.env`. Key runtime variables:
- MT5: `MT5_PATH`, `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`
- Symbols: `OCO_SYMBOLS`
- OCO control: `OCO_TIMEFRAME`, `OCO_BREAKOUT_LOOKBACK`, `OCO_ARM_TTL_SECONDS`, `OCO_LOOP_SECONDS`
- Position management: `OCO_SL_SPACING_MULT`, `OCO_TRAIL_ACTIVATE_R`, `OCO_TRAIL_SPACING_MULT`, `OCO_TIME_STOP_SECONDS`, `OCO_REENTRY_COOLDOWN_SECONDS`
- Risk: `MAX_DRAWDOWN_PCT`, `DAILY_LOSS_LIMIT_PCT`, `WEEKLY_LOSS_LIMIT_PCT`, `MAX_LEVERAGE`

## Docs
- Runbook: `docs/runbook.md`
- Architecture: `docs/architecture.md`
- Data Flow: `docs/data-flow.md`
- Decision Flow: `docs/decision-flow.md`
- Code Reference: `docs/code-reference.md`
- OCO Strategy Spec: `docs/oco_strategy_spec.md`

## Disclaimer
This system is not guaranteed profitable. Markets are non-stationary, and strict risk controls are mandatory.
