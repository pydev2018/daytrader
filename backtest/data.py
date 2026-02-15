"""
Backtest data helpers.
"""

from __future__ import annotations

import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure bid/ask OHLC columns exist.
    Supports:
      - bid/ask mid bars with columns bid, ask
      - bid_* and ask_* OHLC
    """
    if {"bid", "ask"}.issubset(df.columns):
        df["bid_open"] = df["bid"]
        df["bid_high"] = df["bid"]
        df["bid_low"] = df["bid"]
        df["bid_close"] = df["bid"]
        df["ask_open"] = df["ask"]
        df["ask_high"] = df["ask"]
        df["ask_low"] = df["ask"]
        df["ask_close"] = df["ask"]
    required = {"bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise ValueError(f"Missing columns: {sorted(missing)}")
    df["mid_close"] = (df["bid_close"] + df["ask_close"]) / 2.0
    return df
