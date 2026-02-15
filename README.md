# Wolf Trading System (MT5 Python)

Algorithmic trading system for MetaTrader 5 built around multi-timeframe Pristine Method analysis, watchlist-triggered entries, and risk-managed execution. The system runs as a long-lived loop (`WolfEngine`) and can also run in an M15 "sniper" mode for event-driven intraday setups. See `main.py`, `core/market_scanner.py`, `core/watchlist.py`, and `core/sniper/pipeline.py`.

## What It Does
- Connects to MT5, discovers a tradeable universe, and keeps the connection alive via `MT5Connector` (`core/mt5_connector.py`).
- Scans all symbols, builds a watchlist of qualified setups, then waits for M15 trigger patterns before creating `TradeSignal` objects (`core/market_scanner.py`, `core/watchlist.py`, `core/signals.py`).
- Optional M15 Sniper pipeline (fast pass → deep pass → intrabar triggers) producing `ExecutionIntent` objects (`core/sniper/pipeline.py`, `core/sniper/state.py`).
- Enforces hard risk limits, cooldowns, drawdown halts, and persists risk state + trade journal (`risk/risk_manager.py`).
- Executes and manages trades via MT5 with SL/TP validation, broker STOPLEVEL handling, and partial/modify/close support (`execution/trade_executor.py`, `execution/position_monitor.py`).
- Sends Telegram alerts and (optionally) GPT-5.2 chart-based assessments that scale risk (`alerts/telegram.py`, `core/chart_analyst.py`, `core/ai_analyst.py`).
- Backtests the M15 sniper logic with CSV/JSON outputs (`backtest_sniper.py`).

## Supported Markets / Symbols
The tradable universe is built from MT5 symbol groups (`SCAN_GROUPS`) and filtered by exclusions (`EXCLUDE_SYMBOLS`) in `config.py`. Crypto symbols are treated as 24/7 and exempt from forex session rules using `CRYPTO_PREFIXES` in `config.py` and the logic in `utils/market_hours.py`.

## Quickstart (Windows)
1. **Install dependencies**
   - `pip install -r requirements.txt`
2. **Configure environment**
   - Copy `.env.template` → `.env` and set MT5 credentials and optional integrations (`config.py` loads it via `python-dotenv`).
3. **Run**
   - Live: `python main.py`
   - Scan only: `python main.py --scan-only`
   - Status only: `python main.py --status`
   - Conda wrapper: `run.bat main.py`

## Configuration
Primary configuration lives in `config.py`. Secrets and credentials are read from `.env` (see `.env.template`).
Key configuration areas:
- MT5 connection: `MT5_PATH`, `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (`config.py`).
- Risk limits and sizing: `MAX_RISK_PER_TRADE_PCT`, `DAILY_LOSS_LIMIT_PCT`, `MAX_DRAWDOWN_PCT`, `RISK_FACTOR_*` (`config.py`).
- Watchlist flow: `WATCHLIST_SETUP_THRESHOLD`, `WATCHLIST_CHECK_SECONDS`, `WATCHLIST_MAX_AGE_HOURS` (`config.py`).
- Sniper mode: `SNIPER_MODE`, `SNIPER_EXECUTION_STYLE`, `TPR_*`, `RBH_*`, `ECR_*` (`config.py`).
- Chart analysis: `CHART_ANALYSIS_ENABLED`, `OPENAI_API_KEY` (`config.py`, `core/chart_analyst.py`).

## Logs & Data
- Logs: `logs/<logger>.log` (rotating) via `utils/logger.py`.
- Chart analysis artifacts: `logs/chart_analysis/<SYMBOL>/<timestamp>/` (`core/chart_analyst.py`).
- Trade journal: `data/trade_journal.json` (`risk/risk_manager.py`).
- Risk state: `data/risk_state.json` (`risk/risk_manager.py`).
- News cache: `data/news_cache.json` (`core/news_aggregator.py`).

## Testing
- Tests live under `tests/` and use `pytest` (e.g., `tests/test_risk_manager.py`).
- Run: `python -m pytest`
- Gap: `pytest` is not listed in `requirements.txt`; consider adding a `requirements-dev.txt` or adding pytest to dependencies.
- Gap: no integration tests for MT5 order flow or live data; consider adding a mock MT5 adapter or a sandbox test suite.

## Troubleshooting (Common)
- **MT5 initialize failed**: verify MT5 terminal path/credentials and that the terminal is running (`core/mt5_connector.py`).
- **No symbols / empty watchlist**: review `SCAN_GROUPS`, session filters, and spread limits (`config.py`, `utils/market_hours.py`, `core/market_scanner.py`).
- **Orders rejected**: check broker STOPLEVEL constraints and order_check logs (`execution/trade_executor.py`).
- **Chart analysis disabled**: ensure `OPENAI_API_KEY` is set and `CHART_ANALYSIS_ENABLED` is true (`config.py`, `core/chart_analyst.py`).
- **Trading halted**: review risk limits and state in `data/risk_state.json` (`risk/risk_manager.py`).

## Repository Evidence Index
- `main.py` — system entrypoint, `WolfEngine`, runtime loops, CLI flags.
- `core/mt5_connector.py` — MT5 connection, market data, orders, positions.
- `core/market_scanner.py` / `core/watchlist.py` — full scan, watchlist, trigger flow.
- `core/confluence.py` / `core/pristine.py` — analysis and scoring logic.
- `core/sniper/pipeline.py` / `core/sniper/state.py` — M15 sniper mode.
- `execution/trade_executor.py` / `execution/position_monitor.py` — execution + lifecycle.
- `risk/risk_manager.py` / `risk/position_sizer.py` — risk controls + sizing.
- `alerts/telegram.py` / `core/chart_analyst.py` / `core/ai_analyst.py` — alerts and AI.
- `config.py` / `.env.template` — configuration and secrets.
- `backtest_sniper.py` — backtesting entrypoint for the sniper pipeline.
