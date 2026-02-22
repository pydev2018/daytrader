# Phased Grid Trading System (MT5)

Single-strategy codebase for the **Phase-Offset (Zipper) Grid**.

The bot runs one state machine only:
`INIT -> RANGE -> TREND_LOCK -> EXHAUSTION_CONFIRM -> GARBAGE_COLLECT -> RECENTER -> RISK_OFF`

## Strategy Summary
- **RANGE**: deploys interlocked long/short limit ladders with 50% offset.
- **TREND_LOCK**: clamps the losing side during directional runs.
- **EXHAUSTION_CONFIRM**: waits for trend decay before reset.
- **GARBAGE_COLLECT**: closes residual underwater side in chunks.
- **RECENTER**: rebuilds around current regime price.
- **Sleep-safe additions**: optional startup anchor hedge, slow-trend persistence trigger, and active `RISK_OFF` unwind of worst floating-loss positions.
- **Protected capital buckets**: oscillation profit is stored separately (`oscillation_bank`) and trend cleanup/unwind can only consume `trend_buffer` when protection is enabled.

## Quick Start
1. Install deps: `pip install -r requirements.txt`
2. Copy `.env.template` to `.env` and set MT5 credentials.
3. Run live engine: `python main.py`
4. Check status only: `python main.py --status`

## Realistic Backtest
Run multi-symbol historical simulation with dynamic spread, slippage, commission, and swap:

`python -m sim.run_backtest --symbols EURCAD,EURUSD,USDJPY --start 2025-01-01T00:00:00 --end 2025-12-31T23:55:00 --timeframe M5 --balance 10000 --leverage 30 --lot 0.01 --commission 0.0 --swap-long 0.0 --swap-short 0.0 --slip-base 0.1 --slip-range 0.05 --slip-trend 0.10 --output logs/backtest_report.json`

This writes a JSON report with portfolio metrics, per-symbol PnL, phase transitions, and all simulated fills.

## Parameter Sweep
Run a grid search across key strategy controls and rank by return/drawdown score:

`python -m sim.sweep --symbols EURCAD,EURUSD --start 2026-01-01T00:00:00 --end 2026-02-15T23:55:00 --timeframe M5 --step-atr-mults 0.8,0.9,1.0 --step-spread-mults 10,12,14 --trend-on-adx-list 24,25,27 --trend-off-adx-list 18,20,22 --slow-trend-bars-list 4,5,6 --slow-trend-move-steps-list 0.8,1.0,1.2 --cleanup-count-list 1,2 --risk-unwind-list 1,2 --score return_dd --top 10 --output-json logs/sweep_results.json --output-csv logs/sweep_results.csv`

The sweep loads market data once, runs all combinations, and writes ranked results for fast tuning.

## Folder Layout
- `app/` runtime and CLI
- `strategy/` phased-grid state machine, indicators, planner
- `execution/` order intents and MT5 order sync
- `brokers/` MT5 adapter
- `risk/` risk-off guard rules
- `storage/` persisted strategy state
- `config/` validated environment settings
- `tests/` strategy-focused unit tests

## Notes
- MT5 account must be **hedging mode**.
- Symbol adaptation is dynamic (tick size/digits/pip-aware sizing per symbol).
- No legacy grid/oco/scanner logic is retained in runtime flow.
