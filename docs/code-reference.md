# API / Code Reference

## Entrypoints
- **`app/main.py`** — Grid engine CLI entrypoint.
- **`main.py`** — shim that calls `app/main.py`.
- **`backtest/grid_engine.py`** — grid backtest engine.

## Core Modules
### MT5 Integration
**`brokers/mt5.py`**
- Purpose: MT5 connection lifecycle, market data, orders, and history.
- Key interfaces: `connect()`, `ensure_connected()`, `symbol_tick()`, `orders_get()`, `order_send()`.

### Grid Logic
**`grid/engine.py`**
- Purpose: main loop and orchestration.
- Key interfaces: `GridEngine.start()`.

**`grid/anchor.py`**, **`grid/spacing.py`**, **`grid/sizing.py`**
- Purpose: anchor, spacing, and sizing primitives.

**`grid/orders.py`**
- Purpose: rung state, order specs, and comment mapping.

**`grid/state.py`**
- Purpose: persistent grid state.

### Execution
**`execution/order_manager.py`**
- Purpose: idempotent order reconciliation and fill detection.
- Key interfaces: `sync_orders()`, `detect_fills()`, `place_market()`.

### Risk
**`risk/grid_risk.py`**
- Purpose: inventory, leverage, and drawdown halts.
- Key interfaces: `check_limits()`.

### Alerts and Utilities
**`alerts/telegram.py`**
- Purpose: non-blocking Telegram alerts.

**`utils/logger.py`**
- Purpose: structured logging.

### Backtesting
**`backtest/data.py`**, **`backtest/fills.py`**, **`backtest/grid_engine.py`**
- Purpose: bid/ask data normalization and grid backtest simulation.
