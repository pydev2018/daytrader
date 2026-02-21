# Cointegration Pair Finder (Phase 2)

Phase 2 adds an executable pair-check workflow for cointegrated strategy setup.

## What it computes

For a symbol pair (`X`, `Y`) on MT5 bars:

- Engle-Granger regression (`beta`, `intercept`, spread residual)
- ADF test on residual spread (`adf_stat`, `adf_pvalue`)
- Johansen trace test pass/fail
- Hurst exponent of spread
- OU half-life (in bars)
- Rolling-window Engle-Granger pass ratio
- Final pass/fail verdict using configured thresholds

## CLI usage

- Default pair from `COINT_SYMBOLS`:
  - `python main.py --pair-check`
- Explicit symbols:
  - `python main.py --pair-check --pair-x BTCUSD --pair-y ETHUSD`
- Override bars/timeframe:
  - `python main.py --pair-check --pair-x BTCUSD --pair-y ETHUSD --pair-timeframe M5 --pair-bars 2000`

## Required dependency

`statsmodels` is required for ADF and Johansen tests.

Install in your env:

- `conda run -n tradebot pip install statsmodels`

## Output artifacts

Reports are written to:

- `data/pair_checks/pair_check_<X>_<Y>_<timestamp>.json`
- `data/pair_checks/latest_pair_check.json`

## Config knobs

Configured in `config/settings.py`:

- `COINT_SYMBOLS`
- `COINT_TIMEFRAME`
- `COINT_BAR_COUNT`
- `COINT_ADF_ALPHA`
- `COINT_ROLLING_WINDOW`
- `COINT_ROLLING_STEP`
- `COINT_MIN_ROLLING_PASS_RATIO`
- `COINT_MAX_HURST`
- `COINT_MAX_HALF_LIFE_BARS`
