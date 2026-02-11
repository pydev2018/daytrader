"""
===============================================================================
  Technical Indicators — every indicator the Wolf system uses
===============================================================================
  All functions accept a pandas DataFrame with columns:
      open, high, low, close, volume
  and return the DataFrame with new columns appended.
===============================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as cfg

# ═════════════════════════════════════════════════════════════════════════════
#  MOVING AVERAGES
# ═════════════════════════════════════════════════════════════════════════════

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA 9/21/50/200 columns."""
    df["ema_fast"] = ema(df["close"], cfg.EMA_FAST)
    df["ema_medium"] = ema(df["close"], cfg.EMA_MEDIUM)
    df["ema_slow"] = ema(df["close"], cfg.EMA_SLOW)
    df["ema_trend"] = ema(df["close"], cfg.EMA_TREND)
    df["sma_20"] = sma(df["close"], 20)
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  RSI
# ═════════════════════════════════════════════════════════════════════════════

def add_rsi(df: pd.DataFrame, period: int = None) -> pd.DataFrame:
    """Relative Strength Index."""
    period = period or cfg.RSI_PERIOD
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  MACD
# ═════════════════════════════════════════════════════════════════════════════

def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    """MACD line, signal, and histogram."""
    fast = ema(df["close"], cfg.MACD_FAST)
    slow = ema(df["close"], cfg.MACD_SLOW)
    df["macd"] = fast - slow
    df["macd_signal"] = ema(df["macd"], cfg.MACD_SIGNAL)
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  STOCHASTIC
# ═════════════════════════════════════════════════════════════════════════════

def add_stochastic(df: pd.DataFrame) -> pd.DataFrame:
    """Stochastic %K and %D."""
    low_min = df["low"].rolling(window=cfg.STOCH_K).min()
    high_max = df["high"].rolling(window=cfg.STOCH_K).max()
    denom = (high_max - low_min).replace(0, np.nan)
    df["stoch_k"] = 100 * (df["close"] - low_min) / denom
    df["stoch_d"] = df["stoch_k"].rolling(window=cfg.STOCH_D).mean()
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  ADX  (Average Directional Index)
# ═════════════════════════════════════════════════════════════════════════════

def add_adx(df: pd.DataFrame, period: int = None) -> pd.DataFrame:
    """ADX with +DI and -DI."""
    period = period or cfg.ADX_PERIOD

    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, min_periods=period).mean()

    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    df["adx"] = adx
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  BOLLINGER BANDS
# ═════════════════════════════════════════════════════════════════════════════

def add_bollinger_bands(df: pd.DataFrame) -> pd.DataFrame:
    """Bollinger Bands (middle, upper, lower) + bandwidth + %B."""
    mid = sma(df["close"], cfg.BB_PERIOD)
    std = df["close"].rolling(window=cfg.BB_PERIOD).std()
    df["bb_mid"] = mid
    df["bb_upper"] = mid + cfg.BB_STD * std
    df["bb_lower"] = mid - cfg.BB_STD * std
    band_range = (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    df["bb_width"] = band_range / mid
    df["bb_pct_b"] = (df["close"] - df["bb_lower"]) / band_range
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  ATR  (Average True Range)
# ═════════════════════════════════════════════════════════════════════════════

def add_atr(df: pd.DataFrame, period: int = None) -> pd.DataFrame:
    """Average True Range."""
    period = period or cfg.ATR_PERIOD
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift()).abs()
    tr3 = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / period, min_periods=period).mean()
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  CCI  (Commodity Channel Index)
# ═════════════════════════════════════════════════════════════════════════════

def add_cci(df: pd.DataFrame, period: int = None) -> pd.DataFrame:
    period = period or cfg.CCI_PERIOD
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma_tp = tp.rolling(window=period).mean()
    mad = tp.rolling(window=period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["cci"] = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  ICHIMOKU CLOUD
# ═════════════════════════════════════════════════════════════════════════════

def add_ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    """Ichimoku Cloud components."""
    high = df["high"]
    low = df["low"]

    tenkan = (high.rolling(cfg.ICHI_TENKAN).max() + low.rolling(cfg.ICHI_TENKAN).min()) / 2
    kijun = (high.rolling(cfg.ICHI_KIJUN).max() + low.rolling(cfg.ICHI_KIJUN).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(cfg.ICHI_KIJUN)
    senkou_b = ((high.rolling(cfg.ICHI_SENKOU_B).max() + low.rolling(cfg.ICHI_SENKOU_B).min()) / 2).shift(cfg.ICHI_KIJUN)
    chikou = df["close"].shift(-cfg.ICHI_KIJUN)

    df["ichi_tenkan"] = tenkan
    df["ichi_kijun"] = kijun
    df["ichi_senkou_a"] = senkou_a
    df["ichi_senkou_b"] = senkou_b
    df["ichi_chikou"] = chikou
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  VOLUME INDICATORS
# ═════════════════════════════════════════════════════════════════════════════

def add_volume_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """OBV, volume SMA, and volume ratio."""
    # On Balance Volume
    direction = np.sign(df["close"].diff())
    df["obv"] = (direction * df["volume"]).cumsum()

    # Volume moving average
    df["vol_sma"] = sma(df["volume"], 20)

    # Volume ratio (current / average)
    df["vol_ratio"] = df["volume"] / df["vol_sma"].replace(0, np.nan)

    return df


# ═════════════════════════════════════════════════════════════════════════════
#  VWAP  (Volume-Weighted Average Price — intraday)
# ═════════════════════════════════════════════════════════════════════════════

def add_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """Approximate VWAP using tick_volume.  Best for intraday timeframes."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    cumvol = df["volume"].cumsum()
    cumtpvol = (tp * df["volume"]).cumsum()
    df["vwap"] = cumtpvol / cumvol.replace(0, np.nan)
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  PIVOT POINTS
# ═════════════════════════════════════════════════════════════════════════════

def add_pivot_points(df: pd.DataFrame) -> pd.DataFrame:
    """Classic pivot points from previous bar's HLC."""
    prev_h = df["high"].shift(1)
    prev_l = df["low"].shift(1)
    prev_c = df["close"].shift(1)

    pp = (prev_h + prev_l + prev_c) / 3
    df["pivot"] = pp
    df["pivot_r1"] = 2 * pp - prev_l
    df["pivot_s1"] = 2 * pp - prev_h
    df["pivot_r2"] = pp + (prev_h - prev_l)
    df["pivot_s2"] = pp - (prev_h - prev_l)
    df["pivot_r3"] = prev_h + 2 * (pp - prev_l)
    df["pivot_s3"] = prev_l - 2 * (prev_h - pp)
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  DIVERGENCE DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def detect_divergence(
    price: pd.Series,
    indicator: pd.Series,
    lookback: int = 20,
) -> pd.Series:
    """
    Detect bullish/bearish divergence between price and an oscillator.
    Returns a Series with values: 1 (bullish div), -1 (bearish div), 0 (none).
    """
    result = pd.Series(0, index=price.index, dtype=int)

    for i in range(lookback, len(price)):
        window_price = price.iloc[i - lookback : i + 1]
        window_ind = indicator.iloc[i - lookback : i + 1]

        if window_price.isna().any() or window_ind.isna().any():
            continue

        # Find local minima/maxima in the window
        price_min_idx = window_price.idxmin()
        price_max_idx = window_price.idxmax()

        # Bullish divergence: price makes lower low, indicator makes higher low
        recent_price_low = price.iloc[i]
        if i >= 2 and price.iloc[i] < price.iloc[i - lookback]:
            if indicator.iloc[i] > indicator.iloc[i - lookback]:
                result.iloc[i] = 1  # bullish divergence

        # Bearish divergence: price makes higher high, indicator makes lower high
        if i >= 2 and price.iloc[i] > price.iloc[i - lookback]:
            if indicator.iloc[i] < indicator.iloc[i - lookback]:
                result.iloc[i] = -1  # bearish divergence

    return result


# ═════════════════════════════════════════════════════════════════════════════
#  KELTNER CHANNELS  (for BB squeeze detection)
# ═════════════════════════════════════════════════════════════════════════════

def add_keltner(df: pd.DataFrame, period: int = 20, mult: float = 1.5) -> pd.DataFrame:
    """Keltner Channels based on EMA + ATR."""
    mid = ema(df["close"], period)
    if "atr" not in df.columns:
        df = add_atr(df)
    df["kelt_mid"] = mid
    df["kelt_upper"] = mid + mult * df["atr"]
    df["kelt_lower"] = mid - mult * df["atr"]

    # Squeeze: BB inside Keltner = volatility compression
    if "bb_upper" in df.columns and "bb_lower" in df.columns:
        df["squeeze"] = (df["bb_lower"] > df["kelt_lower"]) & (df["bb_upper"] < df["kelt_upper"])
    return df


# ═════════════════════════════════════════════════════════════════════════════
#  FIBONACCI RETRACEMENT
# ═════════════════════════════════════════════════════════════════════════════

FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


def fibonacci_levels(swing_high: float, swing_low: float, direction: str = "up") -> dict[str, float]:
    """
    Calculate Fibonacci retracement levels.
    direction='up'  → retracement of an upswing  (levels between high and low)
    direction='down' → retracement of a downswing
    """
    diff = swing_high - swing_low
    levels = {}
    for lvl in FIB_LEVELS:
        if direction == "up":
            levels[f"fib_{lvl}"] = swing_high - lvl * diff
        else:
            levels[f"fib_{lvl}"] = swing_low + lvl * diff
    return levels


# ═════════════════════════════════════════════════════════════════════════════
#  MASTER FUNCTION — compute everything at once
# ═════════════════════════════════════════════════════════════════════════════

def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full indicator suite on a DataFrame of OHLCV bars."""
    if df is None or df.empty:
        return df

    df = df.copy()
    df = add_moving_averages(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_stochastic(df)
    df = add_adx(df)
    df = add_bollinger_bands(df)
    df = add_atr(df)
    df = add_cci(df)
    df = add_ichimoku(df)
    df = add_volume_indicators(df)
    df = add_vwap(df)
    df = add_pivot_points(df)
    df = add_keltner(df)

    # Divergences
    if "rsi" in df.columns:
        df["rsi_divergence"] = detect_divergence(df["close"], df["rsi"])
    if "macd" in df.columns:
        df["macd_divergence"] = detect_divergence(df["close"], df["macd"])

    return df


# ═════════════════════════════════════════════════════════════════════════════
#  TREND DETERMINATION
# ═════════════════════════════════════════════════════════════════════════════

def determine_trend(df: pd.DataFrame) -> str:
    """
    Determine the dominant trend from the last row of computed indicators.
    Returns: 'BULLISH', 'BEARISH', or 'NEUTRAL'.
    """
    if df is None or df.empty or len(df) < 2:
        return "NEUTRAL"

    last = df.iloc[-1]
    signals = []

    # EMA alignment
    if all(col in df.columns for col in ["ema_fast", "ema_medium", "ema_slow", "ema_trend"]):
        if last["ema_fast"] > last["ema_medium"] > last["ema_slow"] > last["ema_trend"]:
            signals.append(1)
        elif last["ema_fast"] < last["ema_medium"] < last["ema_slow"] < last["ema_trend"]:
            signals.append(-1)
        else:
            signals.append(0)

    # Price vs 200 EMA
    if "ema_trend" in df.columns:
        if last["close"] > last["ema_trend"]:
            signals.append(1)
        elif last["close"] < last["ema_trend"]:
            signals.append(-1)
        else:
            signals.append(0)

    # MACD
    if "macd" in df.columns and "macd_signal" in df.columns:
        if last["macd"] > last["macd_signal"] and last["macd"] > 0:
            signals.append(1)
        elif last["macd"] < last["macd_signal"] and last["macd"] < 0:
            signals.append(-1)
        else:
            signals.append(0)

    # ADX direction
    if "adx" in df.columns and "plus_di" in df.columns:
        if last["adx"] > cfg.ADX_TREND_THRESHOLD:
            if last["plus_di"] > last["minus_di"]:
                signals.append(1)
            else:
                signals.append(-1)

    # Ichimoku cloud
    if "ichi_senkou_a" in df.columns and "ichi_senkou_b" in df.columns:
        cloud_top = max(last.get("ichi_senkou_a", 0), last.get("ichi_senkou_b", 0))
        cloud_bot = min(last.get("ichi_senkou_a", 0), last.get("ichi_senkou_b", 0))
        if last["close"] > cloud_top:
            signals.append(1)
        elif last["close"] < cloud_bot:
            signals.append(-1)
        else:
            signals.append(0)

    if not signals:
        return "NEUTRAL"

    avg = sum(signals) / len(signals)
    if avg > 0.3:
        return "BULLISH"
    elif avg < -0.3:
        return "BEARISH"
    return "NEUTRAL"


def trend_strength(df: pd.DataFrame) -> float:
    """Return 0.0-1.0 trend strength from ADX + EMA alignment."""
    if df is None or df.empty:
        return 0.0

    last = df.iloc[-1]
    score = 0.0

    # ADX contribution (0-0.5)
    if "adx" in df.columns:
        adx_val = last.get("adx", 0)
        if not np.isnan(adx_val):
            score += min(adx_val / 100, 0.5)

    # EMA alignment contribution (0-0.5)
    if all(col in df.columns for col in ["ema_fast", "ema_medium", "ema_slow", "ema_trend"]):
        vals = [last["ema_fast"], last["ema_medium"], last["ema_slow"], last["ema_trend"]]
        if not any(np.isnan(v) for v in vals):
            # Check if perfectly ordered
            if vals == sorted(vals, reverse=True) or vals == sorted(vals):
                score += 0.5
            elif vals[:3] == sorted(vals[:3], reverse=True) or vals[:3] == sorted(vals[:3]):
                score += 0.3

    return min(score, 1.0)
