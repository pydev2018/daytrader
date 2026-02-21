# Cointegrated Strategy System (MT5 Python)

Pure cointegrated strategy branch for MetaTrader 5 (OANDA MT5). Legacy single-asset grid runtime code has been removed from this branch.

## What It Does
- Connects to MT5 and provides a cointegrated-strategy entrypoint (`brokers/mt5.py`, `app/main.py`, `strategies/cointegrated/engine.py`).
- Freezes legacy grid scope to enable clean phase-by-phase cointegrated implementation.

## Quickstart (Windows)
1. Install dependencies: `pip install -r requirements.txt`
2. Configure `.env` from `.env.template`
3. Run:
   - Live: `python main.py`
   - Status: `python main.py --status`

## Configuration
Configuration lives in `config/settings.py` and `.env`. Key variables:
- MT5: `MT5_PATH`, `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`
- Symbols: `GRID_SYMBOLS`
- Risk: `MAX_DRAWDOWN_PCT`, `MAX_INVENTORY_LOTS`, `MAX_LEVERAGE`
- Grid: `GRID_LEVELS`, `ANCHOR_HALFLIFE_SECONDS`, `GRID_SPACING_K_SIGMA`

## Docs
- Architecture: `docs/architecture.md`
- Control flow: `docs/control-flow.md`
- Data flow: `docs/data-flow.md`
- Strategy spec: `docs/grid_strategy_spec.md`
- Runbook: `docs/runbook.md`

## Status
Current branch state is freeze-scope complete for legacy grid removal. Pair finder and spread execution are implemented in upcoming phases.

Archived scripts are organized under `obsolete/`.

## Disclaimer
This system is not guaranteed profitable. Markets are non-stationary, and the strategy is explicitly risk-managed to fail safely under adverse regimes.
