# Algorithmic Trading System Documentation

This document is a comprehensive, line-by-line-derived description of the live grid trading system in this repository. It is intentionally detailed and exhaustive, and it avoids code snippets by design.

## Scope and exclusions

- Scope: live trading system, including orchestration, market data access, grid logic, execution, risk controls, alerts, logging, and tests for these components.
- Excluded: the `backtest` folder and any backtest-only behavior or assumptions. Backtest-related parameters in configuration are explicitly marked as out of scope.

## System goals and operating model

- Strategy type: mean-reversion grid trading with symmetric limit entries around a dynamic center.
- Operating venue: MetaTrader5 (MT5) via the Python MT5 API, with a strict requirement for hedging account mode.
- Core philosophy: adapt spacing and sizing to volatility, cost, and inventory; avoid trading in adverse regimes; manage risk using hard limits and adaptive throttles.
- Multi-symbol support: the system can trade multiple FX symbols simultaneously and rotate symbols based on scan scores.

## High-level architecture

The system is composed of the following cooperating layers:

- Entry and CLI layer: `app/main.py` and the `main.py` shim.
- Configuration and validation: `config/settings.py` with runtime parameter validation on import.
- Broker adapter: `brokers/mt5.py` encapsulates MT5 connectivity, data access, and order operations.
- Core engine: `grid/engine.py` orchestrates the entire trading lifecycle.
- Grid model and mechanics: `grid/state.py`, `grid/orders.py`, `grid/anchor.py`, `grid/spacing.py`, `grid/regime.py`, `grid/sizing.py`.
- Symbol selection: `grid/scanner.py` and `grid/mr_scanner.py`.
- Execution and reconciliation: `execution/order_manager.py` with audit logging.
- Risk management: `risk/grid_risk.py` with persistent state and halting.
- Alerts: `alerts/telegram.py` for non-blocking Telegram notifications.
- Logging: `utils/logger.py` for structured console and rotating file logs.
- Smoke test: `mt5_oanda_smoke_test.py` validates connectivity and broker constraints without placing orders.
- Tests: `tests/*` validate core behavior for live modules.

## Runtime lifecycle

### Startup sequence

1. Logging is initialized via `setup_logging()`, ensuring console output and optional file logs.
2. The system connects to MT5 via `MT5Broker.connect()` and verifies account information.
3. A mandatory account model check ensures hedging is enabled; netting or FIFO accounts are rejected to prevent mismanagement of position-level exits.
4. Symbol selection happens in one of two modes:
   - Auto-scan: `GridScanner` ranks symbols and selects top candidates.
   - Manual: uses `GRID_SYMBOLS` from the environment.
5. Startup cleanup cancels all orphaned pending orders tied to the system magic number to prevent stale orders from prior sessions.
6. For each active symbol, internal tracking structures and estimators are initialized.
7. Any orphaned positions that already exist are adopted into the grid state and assigned exits.
8. A Telegram status alert is sent (if configured), then the main loop begins.

### Main loop structure

Each loop iteration executes at a configured cadence (`GRID_LOOP_SECONDS`), with safeguards and state persistence:

- Market hours gating: no trading on weekends; pre-weekend safeguards begin before close.
- Connection recovery: MT5 connectivity is auto-restored with exponential backoff and jitter.
- Post-weekend reinitialization: estimator state is refreshed after the market reopens to avoid stale anchors and volatility.
- Periodic rescans: when auto-scan is enabled, the scanner can rotate symbols based on evolving conditions.
- Per-symbol processing: the core decision flow runs for each active symbol.
- Retiring symbols: symbols removed by the scanner continue to be managed until all open positions are closed.
- State persistence: grid state and risk state are saved every cycle.
- Status logging: a summary snapshot is emitted periodically for operational visibility.

### Shutdown sequence

When a termination signal is received, the system:

- Sends a Telegram stop status.
- Disconnects from MT5.
- Logs a clean shutdown message.

## Per-symbol decision flow (detailed)

This describes the full decision pipeline executed inside `GridEngine._process_symbol()`:

1. Market data acquisition
   - Fetch the latest tick; validate bid/ask.
   - Load symbol metadata, including tick size and contract size.

2. History maintenance
   - Append mid-price and normalized spread to fixed-length deques.

3. Adaptive estimators
   - Update EMA anchor using a time-aware decay (`EmaAnchor.update()`).
   - Update fast and slow volatility estimates using time-aware EWMA (`VolEstimator.update()`).
   - Compute the volatility ratio for regime classification.

4. Spacing computation
   - Use `compute_spacing()` to set grid spacing from volatility, cost floor, and tick-size rounding.
   - Calculate cost floor using spread plus slippage buffer.

5. Inventory calculation
   - Aggregate live positions by side to compute net inventory.
   - Normalize inventory to a -1..1 ratio against `MAX_INVENTORY_LOTS`.

6. Center adjustment
   - Start from the anchor and skew the center away from inventory to reduce exposure.
   - Maintain existing grid center and spacing unless a reset is needed.

7. Regime classification
   - Compute trend z-score from recent prices.
   - Compute spread ratio versus median spreads.
   - Classify regime as ACTIVE, CAUTION, or PAUSED with a reason code.

8. Risk checks
   - Compute notional exposure using contract size and current price.
   - Consult `GridRiskManager.check_limits()` to determine halts and risk multiplier.
   - Pause the symbol if halted or if the regime is PAUSED.

9. Grid reset logic
   - Reset if the grid is uninitialized, if the center has drifted too far, or if the configured grid level count changes.
   - Rebuild standard rungs and preserve any adopted rungs from past positions.

10. Position adoption
    - Any untracked open positions are attached to rungs and assigned take-profit exits.

11. Edge and entry gating
    - Verify the edge (spacing minus cost floor) meets minimum thresholds.
    - Block entries if the symbol is paused or in pre-weekend wind-down.

12. Sizing
    - Start with `BASE_ORDER_SIZE_LOTS` scaled by risk multiplier.
    - Apply inventory skew and depth taper via `size_for_rung()`.
    - Prevent further buying or selling if inventory is near limits.
    - Halve size during CAUTION regimes.

13. Fill detection
    - Inspect MT5 deal history via `OrderManager.detect_fills()`.
    - Handle ENTRY fills: toggle rung to EXIT and set TP.
    - Handle EXIT fills: toggle rung back to ENTRY and log realized PnL.
    - Deduplicate by deal ID and handle partial fills.

14. TP safety net
    - Ensure every EXIT rung has a TP set on its position.
    - Correct any missing or mismatched TP values on open positions.

15. Inferred closes
    - If a position disappears without a deal record, infer a TP fill to keep state consistent.

16. Desired order generation
    - Build desired ENTRY orders from rungs that are eligible to enter.
    - Enforce minimum volume and entry gating conditions.

17. Risk unwind (if halted)
    - Cancel all pending orders and close all open positions by ticket.
    - Reset rungs to ENTRY state and stop further processing for this cycle.

18. Order synchronization
    - At a configured cadence or on reset/fill, reconcile pending orders with desired orders.
    - Cancel stale orders, replace when price or size deviates beyond thresholds, and respect max pending caps.

19. State updates
    - Save last sync time, last update time, and counters.

## Guardrails and safety controls

This system is built to avoid silent failure modes and dangerous exposure. Key guardrails include:

- Configuration validation: `_validate()` enforces parameter bounds before the system can start.
- Account model requirement: system refuses to run on netting/FIFO accounts to avoid exit mismanagement.
- Market-hours gating: no trading during weekends, plus warmup after open and pre-close entry blocks.
- Pre-weekend wind-down: entries cancelled and far-from-TP positions closed to avoid gap risk.
- Regime gating:
  - Wide spreads or volatility shocks pause the symbol.
  - Strong trends force CAUTION mode with reduced sizing.
- Edge control: entries only occur when spacing exceeds cost floor by a minimum margin.
- Broker constraints:
  - Volume normalized to min/max/step and never rounded up.
  - Stops-level and freeze-level enforced for pending orders.
  - Max pending order limits enforced per symbol and per account.
- Risk manager enforcement:
  - Drawdown, daily loss, weekly loss, leverage, notional, inventory, and margin limits.
  - Permanent halt on max drawdown until manual reset.
  - Safe mode triggered by consecutive errors.
- Orphan handling:
  - Startup cancels orphaned orders.
  - Open orphaned positions are adopted and managed instead of force-closed.
- Connection resiliency:
  - MT5 reconnects with exponential backoff and jitter.
  - Retriable MT5 return codes are handled with measured retries.
- Auditability:
  - All fills and order actions are logged to a JSONL journal.
  - Structured logs with time stamps and rotating file support.

## Adaptation and dynamic behavior

Adaptive behavior is embedded into multiple layers:

- Center adaptation: EMA anchor with half-life control reacts to price drift over time.
- Volatility adaptation: time-aware EWMA uses actual elapsed time between ticks.
- Spacing adaptation: spacing grows with volatility and costs, and rounds to tick size.
- Inventory adaptation:
  - Grid center is skewed against inventory.
  - Order size is skewed against inventory and tapered with depth.
- Risk adaptation:
  - Risk multiplier reduces sizes as drawdown or leverage warnings occur.
  - CAUTION regime halves size during trending conditions.
- Symbol adaptation:
  - Scanner re-evaluates symbols and rotates the active universe.
  - Retired symbols continue to be managed until flat.

## Data persistence and audit trail

The system persists essential state across restarts:

- Grid state: stored as `GRID_STATE_PATH`, includes rungs, center, spacing, and last deal markers.
- Risk state: stored as `RISK_STATE_PATH`, includes halts, loss limits, and equity markers.
- Trade journal: append-only JSONL log at `TRADE_JOURNAL_PATH`.
- Logs: structured logs in `logs/` with rotation and UTC timestamps.

## Configuration model (live system)

The configuration is centralized in `config/settings.py` and validated on import:

### MT5 connection
- `MT5_PATH`, `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, `MT5_TIMEOUT` define broker connectivity.

### Telegram alerts
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` enable asynchronous notifications.

### Logging
- `LOG_LEVEL` controls severity thresholds.

### Runtime identity
- `MAGIC_NUMBER` tags all orders and positions as belonging to this system.

### Symbols and feeds
- `GRID_SYMBOLS` defines default symbols when auto-scan is disabled.
- `GRID_TIMEFRAME` and `GRID_BAR_COUNT` are defined but not used by the live engine (reserved or backtest-only).

### Grid core parameters
- `GRID_LEVELS` number of rungs per side.
- `GRID_RESET_K` reset threshold relative to spacing.
- `GRID_RESET_MIN_SECONDS` minimum time between resets.
- `ANCHOR_HALFLIFE_SECONDS` controls anchor responsiveness.
- `VOL_EWMA_LAMBDA` and `VOL_HORIZON_SECONDS` control volatility smoothing and horizon.
- `GRID_SPACING_K_SIGMA`, `GRID_SPACING_K_COST`, `GRID_SPACING_MIN_TICKS`, `SLIPPAGE_BUFFER_TICKS`, `EDGE_MIN_TICKS` drive spacing and edge gating.
- `BASE_ORDER_SIZE_LOTS`, `SIZE_TAPER_ETA`, `INVENTORY_SKEW_GAMMA`, `CENTER_SKEW_K` control sizing and inventory response.
- `RUNG_STOP_LOSS_SPACINGS` is defined but only used in backtest modules (out of scope).
- `WEEKEND_CLOSE_THRESHOLD_SPACINGS` drives pre-weekend risk reduction.

### Execution control
- `GRID_LOOP_SECONDS` main loop cadence.
- `ORDER_REFRESH_SECONDS` minimum interval between order syncs.
- `REQUOTE_THRESHOLD_TICKS` threshold for order replacement.
- `MAX_PENDING_ORDERS_PER_SYMBOL` caps outstanding pending orders.

### Scanner and rotation
- `AUTO_SCAN_ENABLED`, `SCAN_INTERVAL_SECONDS`, `SCAN_TIMEFRAME`, `SCAN_BAR_COUNT`.
- `MAX_ACTIVE_SYMBOLS`, `MIN_SCAN_SCORE`, `SCAN_UNIVERSE` define selection rules.

### Regime and safety gates
- `SPREAD_PAUSE_MULT`, `VOL_SHOCK_RATIO`, `TREND_SLOPE_Z` define regime thresholds.

### Risk limits
- `MAX_LEVERAGE`, `MAX_DRAWDOWN_PCT`, `DAILY_LOSS_LIMIT_PCT`, `WEEKLY_LOSS_LIMIT_PCT`.
- `MAX_INVENTORY_LOTS`, `MAX_NOTIONAL_MULT_EQUITY`.

### Backtest-only parameters (out of scope)
- `BT_COMMISSION_PER_LOT`, `BT_SWAP_PER_LOT_PER_DAY`, `BT_DEFAULT_SLIPPAGE_TICKS`.

## Function and class catalog

This catalog names all key functions, classes, and methods in the live system and explains their roles.

### Entrypoints

- `main.py`:
  - `main()` is a thin shim that forwards to `app.main.main()` for legacy compatibility.

- `app/main.py`:
  - `show_status()` connects to MT5, prints account and open position summaries, then disconnects.
  - `run_scan()` runs the symbol scanner and prints ranked results with detailed diagnostics.
  - `main()` is the CLI entrypoint, parsing arguments for status, scan, or full engine run.

### Configuration and validation

- `config/settings.py`:
  - `_validate()` performs exhaustive checks for parameter sanity and aborts startup on errors.
  - Module constants define all runtime, risk, scanner, and execution parameters.

### Logging

- `utils/logger.py`:
  - `setup_logging()` builds console and rotating file handlers with UTC timestamps.
  - `get_logger()` returns a namespaced logger and ensures logging is initialized.

### Broker adapter

- `brokers/mt5.py`:
  - `MT5Broker` provides all broker-facing operations with caching and retries.
  - Connection and account methods:
    - `connect()`, `disconnect()`, `ensure_connected()` manage MT5 sessions.
    - `account_info()`, `account_balance()`, `account_equity()`, `account_model()` provide account state.
  - Market data and symbol metadata:
    - `select_symbol()`, `symbol_info()`, `symbol_tick()`, `get_rates()`.
  - Positions and orders:
    - `open_positions()`, `our_positions()`, `pending_orders()`, `history_deals()`.
  - Order operations:
    - `pick_filling_mode()`, `check_order()`, `send_order()`.
    - `cancel_order()`, `modify_order()`, `modify_position_tp()`, `close_position()`.
  - Calculations and validation:
    - `calc_margin()`, `calc_profit()`.
    - `normalize_price()`, `normalize_volume()`, `price_meets_stops_level()`, `get_min_distance()`.
  - Constants:
    - `TF_MAP` defines supported timeframes.
    - `RETRIABLE_RETCODES` lists MT5 codes eligible for retry.

### Core engine

- `grid/engine.py`:
  - `_is_market_open()` implements weekend and session timing rules.
  - `_is_pre_weekend_wind_down()` triggers entry suspension before close.
  - `_signal_handler()` captures termination signals for graceful shutdown.
  - `GridEngine.__init__()` constructs broker, risk, scanner, state, and estimator dependencies.
  - `GridEngine.start()` orchestrates startup, validation, adoption, and loop.
  - `GridEngine._get_scan_universe()` selects scanning candidates.
  - `GridEngine._run_scan()` scores symbols and manages rotation decisions.
  - `GridEngine._retire_symbol()` transitions a symbol to exit-only mode.
  - `GridEngine._process_retiring_symbol()` monitors wind-down until flat.
  - `GridEngine._attach_position_to_rung()` binds live positions to rungs and sets TP.
  - `GridEngine._cancel_all_orphaned_orders()` clears stale pending orders on startup.
  - `GridEngine._adopt_orphaned_positions()` attaches legacy positions into the grid state.
  - `GridEngine._pre_weekend_wind_down()` closes high-risk positions before weekends.
  - `GridEngine._init_symbols()` initializes per-symbol structures and estimators.
  - `GridEngine._main_loop()` runs the lifecycle with market checks, scanning, and persistence.
  - `GridEngine._process_symbol()` executes the per-symbol decision flow.
  - `GridEngine._shutdown()` sends status and disconnects safely.

### Grid state and order modeling

- `grid/state.py`:
  - `GridRung` models a single grid level with entry/exit state and ticket linkage.
  - `GridState` encapsulates per-symbol grid state and last-deal markers.
  - `_rung_from_dict()` and `_state_from_dict()` parse persisted state safely.
  - `load_state()` restores grid state from disk.
  - `save_state()` persists grid state with multi-strategy fallback for Windows locks.
  - `new_state()` builds a fresh state with a conservative deal lookback.

- `grid/orders.py`:
  - `OrderSpec` defines the normalized shape of an intended entry order.
  - `make_comment()` encodes grid ID, rung ID, and state into MT5 order comments.
  - `parse_comment()` decodes comments back into grid identifiers.
  - `build_rungs()` generates the symmetric grid around a center price.
  - `update_rung_on_fill()` toggles rungs between ENTRY and EXIT on fills.
  - `desired_orders()` outputs eligible entry orders and enforces volume minimums.

### Grid mechanics and signals

- `grid/anchor.py`:
  - `EmaAnchor.update()` produces a time-aware EMA anchor for grid center.

- `grid/spacing.py`:
  - `VolEstimator.update()` computes time-aware EWMA volatility of log returns.
  - `VolEstimator.sigma` exposes current volatility.
  - `compute_spacing()` calculates spacing using vol scaling, cost floor, and tick rounding.

- `grid/regime.py`:
  - `RegimeState` encapsulates market regime classification.
  - `compute_trend_z()` computes a log-price trend z-score.
  - `classify_regime()` labels the regime as ACTIVE, CAUTION, or PAUSED.

- `grid/sizing.py`:
  - `inventory_scale()` adjusts size based on inventory imbalance.
  - `depth_taper()` reduces size for outer grid levels.
  - `size_for_rung()` combines base size, skew, and taper for final rung sizing.

### Symbol selection

- `grid/scanner.py`:
  - `SymbolScore` stores the full scoring breakdown for a symbol.
  - `_hurst_exponent()` estimates mean-reversion via rescaled range.
  - `_variance_ratio()` tests for mean reversion via variance ratios.
  - `_oscillation_vs_spread()` measures oscillation amplitude versus trading costs.
  - `_estimate_fills_per_day()` estimates grid crossing frequency.
  - `GridScanner.scan()` scores all symbols and returns a ranked list.
  - `GridScanner._score_symbol()` computes composite scores and verdicts.
  - `FX_MAJORS`, `FX_CROSSES`, `FX_UNIVERSE` define the default scan universe.

- `grid/mr_scanner.py`:
  - `MRScore` stores mean-reversion scoring details.
  - `_clamp()` bounds scoring values to stable ranges.
  - `_timeframe_minutes()` normalizes timeframe strings.
  - `_hurst_exponent()`, `_variance_ratio()`, `_half_life()` estimate mean reversion.
  - `_mean_crossings_per_hour()` estimates oscillation frequency around the mean.
  - `_oscillation_vs_spread()` scores oscillation relative to costs.
  - `MeanReversionScanner.scan()` produces ranked symbol lists.
  - `MeanReversionScanner._score_symbol()` computes composite MR scores.

### Execution and trade lifecycle

- `execution/order_manager.py`:
  - `FillEvent` captures standardized fill details across entry and exit flows.
  - `TradeJournal` provides append-only audit logging:
    - `log_fill()`, `log_order_placed()`, `log_order_cancelled()`, `log_safety()`.
  - `OrderManager` orchestrates order placement and reconciliation:
    - `_pick_filling()` chooses a broker-supported filling mode.
    - `place_limit()` places pending entry orders with stops-level validation.
    - `place_market()` submits market orders for emergency exits.
    - `cancel()` cancels orders with audit logging.
    - `set_exit_tp()` sets position-level TP for exits on hedging accounts.
    - `close_position_by_ticket()` closes a specific open position.
    - `unwind_position()` closes all positions for a symbol with spam protection.
    - `clear_unwind_flag()` resets unwind tracking.
    - `sync_orders()` reconciles desired and existing orders with replace logic.
    - `detect_fills()` builds fill events from MT5 deal history.

### Risk management

- `risk/grid_risk.py`:
  - `RiskStatus` summarizes the current risk state and multipliers.
  - `GridRiskManager` enforces account-level and symbol-level limits:
    - `_day_start_time()`, `_week_start_time()` compute rollovers.
    - `_state_dict()`, `_state_hash()`, `_persist_state()`, `_restore_state()` maintain persisted risk state.
    - `_account_notional()` computes total exposure across all grid positions.
    - `update_equity()`, `reset_daily_if_needed()`, `reset_weekly_if_needed()` manage equity baselines.
    - `record_error()`, `clear_errors()` implement safe-mode logic.
    - `check_limits()` evaluates all risk constraints and updates halt status.
    - `manual_reset()` clears permanent halts after operator intervention.

### Alerts

- `alerts/telegram.py`:
  - `TelegramAlerter` sends non-blocking messages with rate limiting and queue caps.
  - Internal queue and worker:
    - `_send()`, `_on_done()`, `_do_send()` manage async delivery.
  - Alert types:
    - `trade_opened()`, `trade_closed()`, `cycle_complete()`.
    - `safety_event()`, `daily_summary()`, `bot_status()`, `custom()`.

### Smoke test

- `mt5_oanda_smoke_test.py`:
  - `_print_check()` prints MT5 order_check results in a consistent format.
  - `main()` verifies MT5 connectivity, symbol metadata, and order_check behavior for multiple filling modes without placing orders.

### Tests (live system)

The test suite validates core live modules and defensive behaviors:

- `tests/test_orders.py`: rung creation, comment parsing, fill toggling, and entry-only order generation.
- `tests/test_scanner.py`: Hurst and variance ratio estimators.
- `tests/test_regime.py`: trend z-score and regime classification behavior.
- `tests/test_anchor.py`: EMA anchor timing semantics and half-life interpretation.
- `tests/test_spacing.py`: volatility estimator time-awareness and spacing computation invariants.
- `tests/test_sizing.py`: inventory skew, depth taper, and sizing correctness.
- `tests/test_risk.py`: risk limit enforcement, permanent halts, and error tracking.

Backtest-specific tests exist but are excluded from this document by scope.

## Operational guidance (non-code)

- Use `app/main.py` to run status checks, symbol scans, or the full engine.
- Confirm the account is hedging-enabled before enabling live trading.
- Maintain a valid `.env` with MT5 and Telegram values; avoid committing secrets.
- Monitor logs and the trade journal to verify behavior during live runs.

