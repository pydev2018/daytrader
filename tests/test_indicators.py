import pandas as pd

from strategy.indicators import adx, atr_pips


def _sample_df() -> pd.DataFrame:
    base = [100, 101, 102, 103, 102, 101, 102, 103, 104, 103, 102, 101, 100, 101, 102, 103, 104, 105, 104, 103]
    return pd.DataFrame(
        {
            "high": [x + 0.5 for x in base],
            "low": [x - 0.5 for x in base],
            "close": base,
        }
    )


def test_atr_pips_positive():
    df = _sample_df()
    value = atr_pips(df, period=5, pip_size=0.1)
    assert value > 0


def test_adx_non_negative():
    df = _sample_df()
    value = adx(df, period=5)
    assert value >= 0
