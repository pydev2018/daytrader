# API / Code Reference

## Entrypoints
- **`main.py`** — `main()` CLI entrypoint; `WolfEngine` orchestrator with `_main_loop()` and `_main_loop_sniper()`.
- **`backtest_sniper.py`** — `main()` and `run_backtest()` for M15 sniper backtests.
- **`_list_symbols.py`** — MT5 symbol listing utility.
- **`test_chart_analyst.py`** — live test harness for chart analysis pipeline.
- **`run.bat`** — Windows wrapper for conda activation and `python %*`.

## Core Modules
### MT5 Integration
**`core/mt5_connector.py`**
- Purpose: MT5 connection lifecycle, data access, orders, and history.
- Key interfaces: `MT5Connector.connect()`, `ensure_connected()`, `get_symbols_by_groups()`, `get_rates()`, `get_ticks()`, `order_check()`, `order_send()`, `history_deals()`.

### Market Scanning & Signals
**`core/market_scanner.py`**
- Purpose: universe scanning and watchlist population.
- Key interfaces: `MarketScanner.full_scan()`, `scan_single()`, `watchlist_check()`.

**`core/watchlist.py`**
- Purpose: M15 trigger detection and `TradeSignal` creation.
- Key interfaces: `Watchlist.update_from_scan()`, `Watchlist.check_triggers()`, `_create_signal_from_trigger()`.
- Data models: `WatchlistEntry`.

**`core/signals.py`**
- Purpose: signal data model and confidence mapping.
- Key interfaces: `TradeSignal`, `confidence_to_win_probability()`.

**`core/confluence.py`**
- Purpose: multi-timeframe analysis and confluence scoring.
- Key interfaces: `analyze_symbol()`, `analyze_timeframe()`, `compute_confluence_score()`.
- Data models: `SymbolAnalysis`, `TimeframeAnalysis`.

### Pristine / TA Primitives
**`core/pristine.py`**
- Purpose: Pristine Method analysis (candles, pivots, stages, retracements).
- Key interfaces: `classify_candle()`, `find_pivots()`, `classify_stage()`, `detect_pristine_setup()`.

**`core/indicators.py`**, **`core/patterns.py`**, **`core/structures.py`**, **`core/smart_money.py`**
- Purpose: technical indicators, pattern detectors, S/R, and smart-money concepts.

### Sniper Mode (M15)
**`core/sniper/pipeline.py`**
- Purpose: event-driven M15 pipeline with fast/deep pass and intrabar checks.
- Key interfaces: `SniperPipeline.on_bar_close()`, `SniperPipeline.intrabar_check()`.

**`core/sniper/state.py`**
- Purpose: sniper data models and state.
- Key interfaces: `ExecutionIntent`, `SymbolState`, `M15Snapshot`, `FastCandidate`.

**`core/sniper/tpr.py`**, **`core/sniper/rbh.py`**, **`core/sniper/ecr.py`**
- Purpose: setup detection and trigger logic for TPR/RBH/ECR.

**`core/sniper/levels.py`**, **`core/sniper/scoring.py`**
- Purpose: M15 indicator helpers and scoring for sniper setups.

### Execution & Position Management
**`execution/trade_executor.py`**
- Purpose: validate, size, and submit orders; modify SL/TP; close positions.
- Key interfaces: `execute_signal()`, `execute_intent()`, `modify_sl_tp()`, `close_position()`.

**`execution/position_monitor.py`**
- Purpose: manage open positions and react to price/structure changes.
- Key interfaces: `check_all_positions()`, `fast_check_all_positions()`, `handle_closed_positions()`, `check_weekend_protection()`.

### Risk & Sizing
**`risk/risk_manager.py`**
- Purpose: risk gates, halts, cooldowns, and persistence.
- Key interfaces: `can_open_trade()`, `adjusted_risk_pct()`, `record_trade_result()`, `log_trade()`.

**`risk/position_sizer.py`**
- Purpose: compute lot sizes using Kelly + caps.
- Key interface: `compute_position_size()`.

**`risk/kelly.py`**
- Purpose: Kelly fraction calculations.
- Key interfaces: `kelly_fraction()`, `kelly_from_confidence()`.

### Alerts, AI, and News
**`alerts/telegram.py`**
- Purpose: non-blocking Telegram alerts.
- Key interfaces: `TelegramAlerter.trade_opened()`, `trade_closed()`, `custom()`.

**`core/chart_analyst.py`**
- Purpose: chart rendering and GPT-5.2 visual analysis.
- Key interfaces: `analyze_signal_charts()`, `render_all_charts()`.

**`core/ai_analyst.py`**
- Purpose: LLM utilities for trade review and briefings.
- Key interfaces: `review_trade()`, `generate_market_briefing()`, `analyze_news()`.

**`core/news_aggregator.py`**
- Purpose: high-impact event windows and news caching.
- Key interface: `is_high_impact_event_window()`.

### Utilities
**`utils/logger.py`**
- Purpose: structured logging to console and files.
- Key interfaces: `setup_logging()`, `get_logger()`.

**`utils/market_hours.py`**
- Purpose: session gates and crypto exemptions.
- Key interfaces: `is_market_open()`, `is_new_trade_allowed()`, `is_crypto_symbol()`.

### Tests
**`tests/`**
- Pytest-based tests for risk, indicators, signals, and patterns (`tests/test_risk_manager.py`, `tests/test_signals.py`, etc.).

## Feature → File → Function/Class Map
| Feature | File | Function/Class |
| --- | --- | --- |
| MT5 connection & reconnect | `core/mt5_connector.py` | `MT5Connector.connect()`, `ensure_connected()` |
| Universe selection | `core/mt5_connector.py` / `config.py` | `get_symbols_by_groups()`, `SCAN_GROUPS` |
| OHLCV retrieval | `core/mt5_connector.py` | `get_rates()` |
| Confluence scoring | `core/confluence.py` | `compute_confluence_score()` |
| Watchlist trigger detection | `core/watchlist.py` | `check_triggers()` |
| Signal model | `core/signals.py` | `TradeSignal` |
| Sniper TPR | `core/sniper/tpr.py` | `detect_tpr_setup()`, `check_tpr_trigger_on_close()` |
| Sniper RBH | `core/sniper/rbh.py` | `initialize_rbh_state()`, `update_rbh_state()` |
| Sniper ECR | `core/sniper/ecr.py` | `evaluate_ecr()` |
| Risk gate | `risk/risk_manager.py` | `can_open_trade()` |
| Position sizing | `risk/position_sizer.py` | `compute_position_size()` |
| Order execution | `execution/trade_executor.py` | `execute_signal()` / `execute_intent()` |
| Modify SL/TP | `execution/trade_executor.py` | `modify_sl_tp()` |
| Position monitoring | `execution/position_monitor.py` | `check_all_positions()` / `fast_check_all_positions()` |
| Journal persistence | `risk/risk_manager.py` | `log_trade()` |
| Alerts | `alerts/telegram.py` | `TelegramAlerter` |
| Chart analysis | `core/chart_analyst.py` | `analyze_signal_charts()` |
| Market hours gating | `utils/market_hours.py` | `is_market_open()` / `is_new_trade_allowed()` |
| Backtesting (sniper) | `backtest_sniper.py` | `run_backtest()` |

## Repository Evidence Index
- `main.py` — entrypoint and orchestration.
- `core/mt5_connector.py` — MT5 API wrapper.
- `core/market_scanner.py` / `core/watchlist.py` — scan-to-signal flow.
- `core/confluence.py` / `core/pristine.py` — analysis and scoring.
- `core/sniper/*` — M15 sniper pipeline.
- `execution/*` — execution and monitoring.
- `risk/*` — risk rules and sizing.
- `alerts/telegram.py` — alerts.
- `core/chart_analyst.py` / `core/ai_analyst.py` — AI analysis.
- `utils/*` — logging and market sessions.
