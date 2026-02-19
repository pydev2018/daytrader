import pandas as pd

from oco.engine import _compute_breakout_setup, _is_oco_comment


def _sample_rates(rows: int = 120) -> pd.DataFrame:
    base = 1.1000
    data = []
    for i in range(rows):
        high = base + 0.0008 + (i % 5) * 0.00001
        low = base - 0.0008 - (i % 3) * 0.00001
        data.append({"high": high, "low": low})
        base += 0.00001
    return pd.DataFrame(data)


def test_is_oco_comment():
    assert _is_oco_comment("oco:EURUSD:B:123") is True
    assert _is_oco_comment("OCO:EURUSD:S:123") is True
    assert _is_oco_comment("gabc:L1:E") is False


def test_compute_breakout_setup_returns_levels_and_sl_distance():
    rates = _sample_rates(140)
    spread = 0.00012
    point = 0.00001

    setup = _compute_breakout_setup(rates, spread, point)

    assert setup is not None
    assert setup.buy_stop > setup.sell_stop
    assert setup.sl_distance > 0


def test_compute_breakout_setup_returns_none_when_not_enough_bars():
    rates = _sample_rates(10)

    setup = _compute_breakout_setup(rates, spread=0.0001, point=0.00001)

    assert setup is None
