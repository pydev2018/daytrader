# Obsolete Archive

This folder contains scripts moved out of active project paths during repository cleanup.

## Why these were archived

- They are not imported by runtime modules (`app/`, `grid/`, `execution/`, `risk/`) or tests.
- Most are one-off exploratory/backtest analysis scripts with hardcoded assumptions.
- Keeping them out of active paths reduces noise and lowers accidental execution risk.

## Archived files

### backtest/

- `advanced_scanner.py` — experimental single-file scanner prototype.
- `check_levels.py` — one-off parameter experiment script.
- `check_symbol.py` — manual symbol constraint inspection utility.
- `deep_analysis.py` — exploratory historical analysis script.
- `diagnose.py` — ad-hoc diagnostic script.
- `diagnose2.py` — ad-hoc diagnostic script (variant).
- `max_scenario.py` — one-off scenario simulation script.
- `projection.py` — static projection printout script.
- `realistic_backtest.py` — scenario-specific comparison script.
- `scan_intraday_mr.py` — standalone scanner workflow (archived by requested aggressive cleanup).
- `scan_m5_grid_symbols.py` — standalone scanner workflow (archived by requested aggressive cleanup).
- `scan_v2.py` — legacy scanner version script.
- `scan_v3.py` — legacy scanner version script.
- `test_mt5_data.py` — manual MT5 symbol discovery utility.
- `tune_recycle.py` — parameter tuning experiment script.
- `weekend_gaps.py` — standalone weekend-gap analysis script.

### tools/

- `analyze_usage.py` — temporary import-graph analysis utility created for this cleanup.

## Notes

- Active smoke test was moved to `diagnostics/mt5_oanda_smoke_test.py`.
- If any archived script needs revival, move it back to a live path and wire it to docs/tests.
