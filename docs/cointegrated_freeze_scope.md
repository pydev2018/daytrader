# Cointegrated Branch Freeze Scope

This branch is now **pure cointegrated strategy** scope.

## Objective

- Remove legacy single-asset grid runtime code from active tree.
- Keep only shared infrastructure required for MT5 connectivity, logging, alerts, and future cointegrated implementation.

## Removed in Freeze Scope

- Legacy strategy package: `grid/`
- Legacy grid risk module: `risk/grid_risk.py`
- Legacy grid execution module: `execution/order_manager.py`
- Legacy grid-focused unit tests in `tests/`

## Rewired in Freeze Scope

- Entrypoint switched to cointegrated runtime stub:
  - `app/main.py` → `strategies/cointegrated/engine.py`
- Risk package exports neutralized:
  - `risk/__init__.py`

## Kept for Next Phases

- MT5 broker adapter: `brokers/mt5.py`
- Logging utilities: `utils/logger.py`
- Alerts transport: `alerts/telegram.py`
- Configuration base: `config/settings.py`
- Diagnostics entrypoint: `diagnostics/mt5_oanda_smoke_test.py`

## Next Implementation Phases

1. Build pair finder from `cointegration_check.md` (BTC/ETH included).
2. Add spread config/model/state.
3. Implement spread execution engine with leg-risk controls.
4. Add spread risk/unwind/recovery and dry-run tests.
