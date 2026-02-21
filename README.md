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

## Quick Start
1. Install deps: `pip install -r requirements.txt`
2. Copy `.env.template` to `.env` and set MT5 credentials.
3. Run live engine: `python main.py`
4. Check status only: `python main.py --status`

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
