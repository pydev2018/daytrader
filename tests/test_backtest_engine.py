from datetime import datetime, timedelta, timezone

import pandas as pd

from sim.config import BacktestConfig
from sim.engine import BacktestEngine
from sim.types import SymbolSpec


def _mock_df() -> pd.DataFrame:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    price = 1.1000
    for i in range(220):
        ts = start + timedelta(minutes=5 * i)
        wave = 0.0006 if i % 2 == 0 else -0.0005
        open_p = price
        close = price + wave
        high = max(open_p, close) + 0.0003
        low = min(open_p, close) - 0.0003
        rows.append(
            {
                "time": ts,
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
                "spread": 10,
            }
        )
        price = close
    return pd.DataFrame(rows)


def test_backtest_engine_runs_and_returns_report():
    df = _mock_df()
    cfg = BacktestConfig(
        start=df.iloc[0]["time"],
        end=df.iloc[-1]["time"],
        initial_balance=10000.0,
        lot_size=0.01,
        start_with_anchor=True,
    )
    specs = {
        "EURUSD": SymbolSpec(
            point=0.00001,
            pip_size=0.0001,
            contract_size=100000.0,
            pip_value_per_lot=10.0,
        )
    }
    engine = BacktestEngine(config=cfg, market_data={"EURUSD": df}, specs=specs)
    report = engine.run()

    assert "final_equity" in report
    assert "max_drawdown_pct" in report
    assert isinstance(report["events"], list)
