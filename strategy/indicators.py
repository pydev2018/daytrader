from __future__ import annotations

import numpy as np
import pandas as pd

from .types import TrendDirection


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr_pips(df: pd.DataFrame, period: int, pip_size: float) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    if np.isnan(atr) or pip_size <= 0:
        return 0.0
    return float(atr / pip_size)


def adx(df: pd.DataFrame, period: int = 14) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)

    atr_series = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_series.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr_series.replace(0, np.nan))

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    value = dx.rolling(period).mean().iloc[-1]
    if np.isnan(value):
        return 0.0
    return float(value)


def trend_direction(df: pd.DataFrame, fast_period: int, slow_period: int) -> TrendDirection:
    fast = ema(df["close"], fast_period)
    slow = ema(df["close"], slow_period)
    if len(fast) < 2 or len(slow) < 2:
        return TrendDirection.FLAT

    fast_slope = fast.iloc[-1] - fast.iloc[-2]
    slow_slope = slow.iloc[-1] - slow.iloc[-2]

    if fast.iloc[-1] > slow.iloc[-1] and fast_slope > 0 and slow_slope > 0:
        return TrendDirection.UP
    if fast.iloc[-1] < slow.iloc[-1] and fast_slope < 0 and slow_slope < 0:
        return TrendDirection.DOWN
    return TrendDirection.FLAT


def kaufman_er(df: pd.DataFrame, period: int = 10) -> float:
    close = df["close"]
    if len(close) <= period:
        return 0.0
    change = abs(close.iloc[-1] - close.iloc[-(period+1)])
    volatility = abs(close.diff()).tail(period).sum()
    if volatility == 0:
        return 0.0
    return float(change / volatility)


def linear_regression_r2(df: pd.DataFrame, period: int = 14) -> float:
    close = df["close"]
    if len(close) < period:
        return 0.0
    y = close.tail(period).values
    x = np.arange(period)
    correlation_matrix = np.corrcoef(x, y)
    correlation_xy = correlation_matrix[0, 1]
    r_squared = correlation_xy**2
    if np.isnan(r_squared):
        return 0.0
    return float(r_squared)


def donchian_channel(df: pd.DataFrame, period: int = 20) -> tuple[float, float]:
    if len(df) < period:
        return 0.0, 0.0
    high = df["high"].tail(period).max()
    low = df["low"].tail(period).min()
    return float(high), float(low)


def is_volatility_squeeze(df: pd.DataFrame, period: int = 20) -> bool:
    if len(df) < period:
        return False
    close = df["close"]
    high = df["high"]
    low = df["low"]
    
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    bb_upper = sma + (2.0 * std)
    bb_lower = sma - (2.0 * std)
    
    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period).mean()
    kc_upper = sma + (1.5 * atr)
    kc_lower = sma - (1.5 * atr)
    
    squeeze_on = (bb_upper.iloc[-1] < kc_upper.iloc[-1]) and (bb_lower.iloc[-1] > kc_lower.iloc[-1])
    return bool(squeeze_on)
