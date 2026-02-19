# Code Reference — Module and API Guide

## Entry and Runtime Modules

### main.py

- Thin shim to `app.main.main()`.

### app/main.py

- `main()`: sets logging, parses CLI, starts OCO engine.
- `show_status()`: prints account/position/pending summary.

## Strategy Runtime

### oco/engine.py

Primary orchestrator class: `OcoEngine`.

Key dataclasses:

- `PendingSpec`: order intent representation for pending stops.
- `OcoSymbolState`: mutable in-memory strategy state per symbol.
- `BreakoutSetup`: computed breakout levels + initial SL distance.
- `SymbolTelemetry`: per-symbol runtime metrics.

Top-level helpers:

- `_is_market_open()`: weekend/session gate.
- `_is_oco_comment()`: strategy order/position ownership marker.
- `_position_side()`: MT5 type to side mapping.
- `_compute_breakout_setup()`: breakout + ATR/buffer calculation.

`OcoEngine` methods:

- Lifecycle:
  - `start()`
  - `_main_loop()`
- Per-symbol processing:
  - `_process_symbol()`
  - `_ensure_oco_orders()`
  - `_manage_open_position()`
- Setup/pace adaptivity:
  - `_get_cached_setup()`
  - `_effective_setup_refresh_seconds()`
  - `_compute_loop_sleep()`
- Safety/cleanup:
  - `_flatten_symbol()`
  - `_cancel_all_symbol_pending()`
  - `_cancel_oco_pending()`
  - `_cancel_all_pending_orders()`
  - `_adopt_open_positions()`
- Instrumentation:
  - `_record_symbol_timing()`
  - `_log_telemetry()`
  - `_log_status()`
  - `_bcall()` / `_ocall()` wrappers

## Execution Layer

### oco/order_manager.py

Class: `OcoOrderManager`

- `place_stop(order, current_price)`
  - Validates symbol visibility, normalizes values, checks stops-level, submits pending stop.
- `cancel(ticket, reason)`
  - Removes pending order by ticket.
- `close_position_by_ticket(symbol, position_ticket, volume, side, reason)`
  - Closes specific hedged position.
- `unwind_position(symbol, inventory_lots)`
  - Emergency flatten with duplicate-send suppression.

## Risk Layer

### risk/manager.py

Dataclasses:

- `RiskStatus`
- `RiskCycleContext`

Class: `RiskManager`

- Persistence and baselines:
  - `_restore_state()`, `_persist_state()`
  - day/week start handling
- Runtime control:
  - `check_limits(...)`
  - `record_error()`
  - `record_success()`
  - `begin_cycle()`

Halt reasons currently produced:

- `account_data_missing`
- `inventory_limit`
- `max_drawdown`
- `daily_loss_limit`
- `weekly_loss_limit`
- `leverage_limit`
- `notional_limit`
- `symbol_notional_limit`
- `margin_level`
- `consecutive_errors`

## Broker Adapter Layer

### brokers/mt5.py

Class: `MT5Broker`

Responsibilities:

- Connection and reconnection management.
- Account introspection and account model checks.
- Symbol/tick/rates retrieval.
- Strategy-scoped positions/orders/history filtering by magic number.
- Order request validation and submission with retry semantics.
- Price/volume normalization and stops-level utilities.

Notable behavior:

- Symbol info cache with short TTL.
- `send_order` retries only on retriable broker return codes.
- Fallback filling mode selection logic for broker compatibility.

## Alerts and Logging

### alerts/telegram.py

Class: `TelegramAlerter`

- Non-blocking message queue using single-thread executor.
- Internal rate limiting and bounded pending queue.
- Convenience methods for status/safety/trade events.

### utils/logger.py

- `setup_logging(name="wolf")`:
  - UTC timestamps.
  - Console + rotating file output.
  - Windows-safe fallback if file locks occur.
- `get_logger(module)`:
  - Child logger helper under root `wolf` logger.

## Configuration

### config/settings.py

Single source of truth for runtime/env configuration and startup validation.

High-impact sections:

- MT5 connection, magic number, symbols/timeframe
- OCO strategy knobs (breakout, buffer, SL, trailing, time-stop)
- Risk limits
- Performance knobs (setup refresh, telemetry interval, adaptive loop)

## Tests

### tests/test_oco_engine.py

- Validates OCO comment parsing helper.
- Validates breakout setup generation success/failure paths.
