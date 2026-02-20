# Grid Trading System (MT5 Python)

Grid-only algorithmic trading system for MetaTrader 5 (OANDA MT5). The system runs a regime-aware, cost-aware grid that places a ladder of buy/sell limits around a dynamic anchor, captures mean-reversion, and enforces strict inventory and drawdown controls.

## What It Does
- Connects to MT5 and maintains a live grid on configured symbols (`brokers/mt5.py`, `grid/engine.py`).
- Computes anchor, spacing, and regime to decide when to trade (`grid/anchor.py`, `grid/spacing.py`, `grid/regime.py`).
- Reconciles orders idempotently and detects fills (`execution/order_manager.py`).
- Enforces risk limits with kill-switches and unwind logic (`risk/grid_risk.py`).
- Persists grid state and risk state to disk.

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

## Backtesting
Use `backtest/grid_engine.py` with bid/ask data.

Archived scripts are organized under `obsolete/`.

## Disclaimer
This system is not guaranteed profitable. Markets are non-stationary, and the strategy is explicitly risk-managed to fail safely under adverse regimes.
