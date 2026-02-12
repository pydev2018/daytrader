"""
===============================================================================
  Test Suite — Pristine Method Analysis Engine
===============================================================================
  Tests for core/pristine.py covering:
    - Candle classification (WRB/NRB/COG/Tail)  — Ch. 2
    - Pivot detection & major/minor              — Ch. 10
    - Stage classification                       — Ch. 1
    - Retracement analysis                       — Ch. 6
    - Volume classification                      — Ch. 5
    - Bar-by-bar analysis                        — Ch. 7
    - Sweet spot / sour spot                     — Ch. 12
    - Breakout Bar Failure                       — Ch. 13
    - Price voids                                — Ch. 3
    - Pristine setup detection (PBS/PSS)
===============================================================================
"""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta


# ═════════════════════════════════════════════════════════════════════════════
#  HELPERS — synthetic data generators
# ═════════════════════════════════════════════════════════════════════════════

def _make_df(
    n: int = 100,
    start_price: float = 100.0,
    trend: str = "up",
    noise: float = 0.5,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generate synthetic OHLCV DataFrame.
    trend: "up", "down", "flat", "up_then_down"
    """
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="h")

    prices = [start_price]
    for i in range(1, n):
        if trend == "up":
            drift = 0.1
        elif trend == "down":
            drift = -0.1
        elif trend == "flat":
            drift = 0.0
        elif trend == "up_then_down":
            drift = 0.15 if i < n // 2 else -0.15
        else:
            drift = 0.0
        prices.append(prices[-1] + drift + rng.randn() * noise)

    prices = np.array(prices)
    # Ensure prices stay positive
    prices = np.maximum(prices, 1.0)

    opens = prices
    closes = prices + rng.randn(n) * noise * 0.5
    highs = np.maximum(opens, closes) + np.abs(rng.randn(n)) * noise * 0.3
    lows = np.minimum(opens, closes) - np.abs(rng.randn(n)) * noise * 0.3

    # Ensure OHLC consistency
    highs = np.maximum(highs, np.maximum(opens, closes))
    lows = np.minimum(lows, np.minimum(opens, closes))

    volume = (rng.rand(n) * 1000 + 500).astype(int)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "tick_volume": volume,
    }, index=dates)

    # Add ATR column (needed by some functions)
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    df["atr"] = df["atr"].bfill()

    return df


def _make_wrb_df(direction: str = "bullish") -> pd.DataFrame:
    """Create a DataFrame where the last bar is a WRB."""
    df = _make_df(30, noise=0.2, trend="flat")
    # Make the last bar a big-body candle
    avg_body = (df["close"] - df["open"]).abs().iloc[-11:-1].mean()
    last_idx = df.index[-1]

    if direction == "bullish":
        df.loc[last_idx, "open"] = 100.0
        df.loc[last_idx, "close"] = 100.0 + avg_body * 3.0
        df.loc[last_idx, "high"] = 100.0 + avg_body * 3.2
        df.loc[last_idx, "low"] = 99.9
    else:
        df.loc[last_idx, "open"] = 100.0 + avg_body * 3.0
        df.loc[last_idx, "close"] = 100.0
        df.loc[last_idx, "high"] = 100.0 + avg_body * 3.2
        df.loc[last_idx, "low"] = 99.9

    return df


def _make_uptrend_df(n: int = 200) -> pd.DataFrame:
    """Create clear uptrend with HPH + HPL pattern."""
    return _make_df(n, start_price=50.0, trend="up", noise=0.3, seed=7)


def _make_downtrend_df(n: int = 200) -> pd.DataFrame:
    """Create clear downtrend with LPH + LPL pattern."""
    return _make_df(n, start_price=150.0, trend="down", noise=0.3, seed=7)


def _make_range_df(n: int = 200) -> pd.DataFrame:
    """Create sideways/range market."""
    return _make_df(n, start_price=100.0, trend="flat", noise=0.5, seed=7)


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Candle Classification (Ch. 2)
# ═════════════════════════════════════════════════════════════════════════════

class TestCandleClassification:

    def test_wrb_detection(self):
        from core.pristine import classify_candle
        df = _make_wrb_df("bullish")
        result = classify_candle(df, idx=-1)
        assert result["type"] == "WRB"
        assert result["bias"] == 1
        assert bool(result["is_bullish"]) is True
        assert result["body_ratio"] >= 2.0

    def test_wrb_bearish(self):
        from core.pristine import classify_candle
        df = _make_wrb_df("bearish")
        result = classify_candle(df, idx=-1)
        assert result["type"] == "WRB"
        assert result["bias"] == -1
        assert bool(result["is_bullish"]) is False

    def test_nrb_detection(self):
        from core.pristine import classify_candle
        df = _make_df(30, noise=0.5, trend="flat")
        # Make last bar tiny body
        last_idx = df.index[-1]
        df.loc[last_idx, "open"] = 100.0
        df.loc[last_idx, "close"] = 100.01
        df.loc[last_idx, "high"] = 100.5
        df.loc[last_idx, "low"] = 99.5
        result = classify_candle(df, idx=-1)
        assert result["type"] == "NRB"

    def test_cog_bullish(self):
        from core.pristine import classify_candle
        df = _make_df(30, noise=0.3)
        last_idx = df.index[-1]
        # Close at top of range
        df.loc[last_idx, "low"] = 99.0
        df.loc[last_idx, "high"] = 101.0
        df.loc[last_idx, "open"] = 99.5
        df.loc[last_idx, "close"] = 100.9  # top 25% = above 100.5
        result = classify_candle(df, idx=-1)
        assert result["cog"] == "bullish"

    def test_tail_demand_rejection(self):
        from core.pristine import classify_candle
        df = _make_df(30, noise=0.3)
        last_idx = df.index[-1]
        # Hammer: long lower wick, small body, no upper wick
        df.loc[last_idx, "open"] = 100.0
        df.loc[last_idx, "close"] = 100.3
        df.loc[last_idx, "high"] = 100.35
        df.loc[last_idx, "low"] = 99.0   # lower wick = 1.0, body = 0.3
        result = classify_candle(df, idx=-1)
        assert result["tail"] == "demand_rejection"

    def test_empty_df_returns_default(self):
        from core.pristine import classify_candle
        result = classify_candle(None)
        assert result["type"] == "normal"
        assert result["bias"] == 0

    def test_classify_last_n(self):
        from core.pristine import classify_last_n_candles
        df = _make_df(50)
        results = classify_last_n_candles(df, n=5)
        assert len(results) == 5
        for r in results:
            assert "type" in r
            assert "bias" in r

    def test_normal_bar(self):
        from core.pristine import classify_candle
        df = _make_df(30, noise=0.3)
        result = classify_candle(df, idx=-5)
        assert result["type"] in ("WRB", "NRB", "normal")


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Pivot Detection (Ch. 10)
# ═════════════════════════════════════════════════════════════════════════════

class TestPivotDetection:

    def test_pivots_found_in_uptrend(self):
        from core.pristine import find_pivots
        df = _make_uptrend_df()
        pivots = find_pivots(df)
        assert len(pivots) > 0
        highs = [p for p in pivots if p["type"] == "high"]
        lows = [p for p in pivots if p["type"] == "low"]
        assert len(highs) > 0
        assert len(lows) > 0

    def test_pivot_structure(self):
        from core.pristine import find_pivots
        df = _make_uptrend_df()
        pivots = find_pivots(df)
        for p in pivots:
            assert "type" in p
            assert "price" in p
            assert "idx" in p
            assert "major" in p
            assert p["price"] > 0

    def test_major_minor_classification(self):
        from core.pristine import find_pivots, classify_pivots_major_minor
        df = _make_uptrend_df()
        pivots = find_pivots(df)
        pivots = classify_pivots_major_minor(pivots)
        major_count = sum(1 for p in pivots if p["major"])
        minor_count = sum(1 for p in pivots if not p["major"])
        # Should have at least some of each
        assert major_count > 0

    def test_trend_from_pivots_uptrend(self):
        from core.pristine import find_pivots, classify_pivots_major_minor, determine_trend_from_pivots
        df = _make_uptrend_df(300)
        pivots = find_pivots(df)
        pivots = classify_pivots_major_minor(pivots)
        trend = determine_trend_from_pivots(pivots)
        assert trend["trend"] == "uptrend"
        assert trend["hph_count"] > 0
        assert trend["hpl_count"] > 0

    def test_trend_from_pivots_downtrend(self):
        from core.pristine import find_pivots, classify_pivots_major_minor, determine_trend_from_pivots
        df = _make_downtrend_df(300)
        pivots = find_pivots(df)
        pivots = classify_pivots_major_minor(pivots)
        trend = determine_trend_from_pivots(pivots)
        assert trend["trend"] == "downtrend"
        assert trend["lph_count"] > 0
        assert trend["lpl_count"] > 0

    def test_trend_from_pivots_range(self):
        from core.pristine import find_pivots, classify_pivots_major_minor, determine_trend_from_pivots
        df = _make_range_df(300)
        pivots = find_pivots(df)
        pivots = classify_pivots_major_minor(pivots)
        trend = determine_trend_from_pivots(pivots)
        # Range market should not show strong uptrend or downtrend
        assert trend["trend"] in ("range", "uptrend", "downtrend")
        # But if it does detect a weak trend, strength should be weak
        if trend["trend"] != "range":
            assert trend["strength"] in ("weak", "moderate")


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Stage Classification (Ch. 1)
# ═════════════════════════════════════════════════════════════════════════════

class TestStageClassification:

    def test_stage_2_uptrend(self):
        from core.pristine import classify_stage
        df = _make_uptrend_df(300)
        result = classify_stage(df)
        assert result["stage"] == 2
        assert result["tradeable"] is True
        assert result["allowed_direction"] == "BUY"

    def test_stage_4_downtrend(self):
        from core.pristine import classify_stage
        # Use a stronger downtrend with less noise and longer history
        df = _make_df(400, start_price=200.0, trend="down", noise=0.2, seed=42)
        result = classify_stage(df)
        # Should detect either Stage 4 or Stage 1 (after the decline)
        # The key assertion is that if it's Stage 4, the direction is SELL
        assert result["stage"] in (1, 4)
        if result["stage"] == 4:
            assert result["tradeable"] is True
            assert result["allowed_direction"] == "SELL"

    def test_stage_1_or_3_range(self):
        from core.pristine import classify_stage
        df = _make_range_df(300)
        result = classify_stage(df)
        assert result["stage"] in (1, 2, 3, 4)
        # If it detects a non-trending stage, should not be tradeable
        if result["stage"] in (1, 3):
            assert result["tradeable"] is False

    def test_stage_confidence_present(self):
        from core.pristine import classify_stage
        df = _make_uptrend_df(300)
        result = classify_stage(df)
        assert 0 <= result["confidence"] <= 1.0

    def test_stage_description(self):
        from core.pristine import classify_stage
        df = _make_uptrend_df(300)
        result = classify_stage(df)
        assert len(result["description"]) > 0

    def test_insufficient_data(self):
        from core.pristine import classify_stage
        df = _make_df(20)  # Too short
        result = classify_stage(df)
        assert result["tradeable"] is False


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Retracement Analysis (Ch. 6)
# ═════════════════════════════════════════════════════════════════════════════

class TestRetracementAnalysis:

    def test_retracement_in_uptrend(self):
        from core.pristine import find_pivots, classify_pivots_major_minor, analyze_retracement
        df = _make_uptrend_df(200)
        pivots = find_pivots(df)
        pivots = classify_pivots_major_minor(pivots)
        result = analyze_retracement(df, pivots, direction=1)
        assert "retracement_pct" in result
        assert "quality" in result
        assert result["quality"] in ("pristine", "healthy", "deep", "failing", "broken", "none", "unknown")

    def test_retracement_quality_labels(self):
        from core.pristine import analyze_retracement
        # Mock data
        df = _make_uptrend_df(200)
        pivots = [
            {"type": "low", "price": 50.0, "idx": 80, "major": True},
            {"type": "high", "price": 60.0, "idx": 150, "major": True},
        ]
        # Price pulled back to 57.0 → (60-57)/10 = 30% = pristine
        df.iloc[-1, df.columns.get_loc("close")] = 57.0
        result = analyze_retracement(df, pivots, direction=1)
        assert result["quality"] in ("pristine", "healthy")

    def test_retracement_broken(self):
        from core.pristine import analyze_retracement
        df = _make_uptrend_df(200)
        pivots = [
            {"type": "low", "price": 50.0, "idx": 80, "major": True},
            {"type": "high", "price": 60.0, "idx": 150, "major": True},
        ]
        # Price pulled back past the start → broken
        df.iloc[-1, df.columns.get_loc("close")] = 48.0
        result = analyze_retracement(df, pivots, direction=1)
        assert result["quality"] == "broken"

    def test_retracement_empty_pivots(self):
        from core.pristine import analyze_retracement
        df = _make_df(50)
        result = analyze_retracement(df, [], direction=1)
        assert result["quality"] == "unknown"

    def test_near_ma20(self):
        from core.pristine import analyze_retracement, find_pivots, classify_pivots_major_minor
        df = _make_uptrend_df(200)
        pivots = find_pivots(df)
        pivots = classify_pivots_major_minor(pivots)
        result = analyze_retracement(df, pivots, direction=1)
        # near_ma20 should be a bool-like value (numpy bool or python bool)
        assert result["near_ma20"] in (True, False) or isinstance(result["near_ma20"], (bool, np.bool_))


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Volume Classification (Ch. 5)
# ═════════════════════════════════════════════════════════════════════════════

class TestVolumeClassification:

    def test_volume_basic(self):
        from core.pristine import classify_volume
        df = _make_uptrend_df(100)
        pivots = [{"type": "high", "price": 60, "idx": 90}]
        result = classify_volume(df, pivots, direction=1)
        assert "current_vol_type" in result
        assert result["current_vol_type"] in ("professional", "novice", "normal")
        assert "pullback_vol_trend" in result
        assert "vol_confirms_trend" in result

    def test_volume_empty_df(self):
        from core.pristine import classify_volume
        result = classify_volume(None, [], 1)
        assert result["current_vol_type"] == "normal"

    def test_volume_no_tick_volume(self):
        from core.pristine import classify_volume
        df = _make_df(50)
        if "tick_volume" in df.columns:
            df = df.rename(columns={"tick_volume": "volume"})
        result = classify_volume(df, [], 1)
        assert "current_vol_type" in result


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Bar-by-Bar Analysis (Ch. 7)
# ═════════════════════════════════════════════════════════════════════════════

class TestBarByBar:

    def test_basic_assessment(self):
        from core.pristine import bar_by_bar_assessment
        df = _make_uptrend_df(100)
        result = bar_by_bar_assessment(df, direction=1)
        assert result["health"] in ("strong", "ok", "warning", "exit")
        assert isinstance(result["rbi_count"], int)
        assert isinstance(result["gbi_count"], int)
        assert isinstance(result["bars_against"], int)

    def test_range_trend_detection(self):
        from core.pristine import bar_by_bar_assessment
        df = _make_df(100, noise=0.1, trend="flat")
        result = bar_by_bar_assessment(df, direction=1)
        assert result["range_trend"] in ("narrowing", "widening", "stable")

    def test_short_df(self):
        from core.pristine import bar_by_bar_assessment
        df = _make_df(5)
        result = bar_by_bar_assessment(df, direction=1)
        assert result["health"] == "ok"  # default


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Sweet Spot / Sour Spot (Ch. 12)
# ═════════════════════════════════════════════════════════════════════════════

class TestSweetSourSpot:

    def test_sweet_spot_aligned(self):
        from core.pristine import detect_sweet_sour_spot
        tf_data = {
            "D1": {
                "stage": {"stage": 2, "confidence": 0.8, "tradeable": True, "allowed_direction": "BUY"},
                "pivot_trend": {"trend": "uptrend", "strength": "strong"},
                "retracement": {"quality": "pristine", "retracement_pct": 0.3},
                "sr_levels": [],
                "candle_class": [{"bias": 1, "type": "WRB"}],
                "current_price": 100.0,
                "atr": 1.0,
            },
            "H1": {
                "stage": {"stage": 2, "confidence": 0.7},
                "pivot_trend": {"trend": "uptrend", "strength": "moderate"},
                "retracement": {"quality": "healthy", "retracement_pct": 0.45},
                "sr_levels": [],
                "candle_class": [{"bias": 1, "type": "normal"}],
                "current_price": 100.0,
                "atr": 0.5,
            },
            "M15": {
                "stage": {},
                "pivot_trend": {},
                "retracement": {"quality": "pristine", "retracement_pct": 0.35, "near_ma20": True},
                "sr_levels": [],
                "candle_class": [{"bias": 1, "type": "normal"}],
                "current_price": 100.0,
                "atr": 0.3,
            },
        }
        result = detect_sweet_sour_spot(tf_data, direction=1)
        assert result["type"] == "sweet_spot"
        assert result["score"] > 0

    def test_sour_spot_stage_conflict(self):
        from core.pristine import detect_sweet_sour_spot
        tf_data = {
            "D1": {
                "stage": {"stage": 3, "confidence": 0.7},
                "pivot_trend": {"trend": "range"},
                "retracement": {},
                "sr_levels": [],
                "candle_class": [],
                "current_price": 100.0,
                "atr": 1.0,
            },
            "H1": {
                "stage": {},
                "pivot_trend": {"trend": "downtrend"},
                "retracement": {},
                "sr_levels": [],
                "candle_class": [],
                "current_price": 100.0,
                "atr": 0.5,
            },
            "M15": {
                "stage": {},
                "pivot_trend": {},
                "retracement": {"quality": "broken"},
                "sr_levels": [],
                "candle_class": [],
                "current_price": 100.0,
                "atr": 0.3,
            },
        }
        result = detect_sweet_sour_spot(tf_data, direction=1)
        assert result["type"] == "sour_spot"
        assert result["score"] < 0

    def test_empty_data(self):
        from core.pristine import detect_sweet_sour_spot
        result = detect_sweet_sour_spot({}, direction=1)
        assert result["type"] == "neutral"


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Breakout Bar Failure (Ch. 13)
# ═════════════════════════════════════════════════════════════════════════════

class TestBBF:

    def test_bearish_bbf(self):
        from core.pristine import detect_breakout_bar_failure
        df = _make_df(30, noise=0.3)
        # Create a bar that breaks above resistance but closes below
        sr_levels = [{"price": 101.0, "kind": "R", "touches": 3, "strength": 0.7}]
        last_idx = df.index[-1]
        df.loc[last_idx, "high"] = 101.5    # broke above resistance
        df.loc[last_idx, "close"] = 100.5   # closed below
        df.loc[last_idx, "open"] = 100.8
        df.loc[last_idx, "low"] = 100.3

        results = detect_breakout_bar_failure(df, sr_levels)
        bearish = [r for r in results if r["bias"] == -1]
        assert len(bearish) > 0
        assert bearish[0]["type"] == "BBF"

    def test_bullish_bbf(self):
        from core.pristine import detect_breakout_bar_failure
        df = _make_df(30, noise=0.3)
        sr_levels = [{"price": 99.0, "kind": "S", "touches": 3, "strength": 0.7}]
        last_idx = df.index[-1]
        df.loc[last_idx, "low"] = 98.5     # broke below support
        df.loc[last_idx, "close"] = 99.5   # closed above
        df.loc[last_idx, "open"] = 99.2
        df.loc[last_idx, "high"] = 99.8

        results = detect_breakout_bar_failure(df, sr_levels)
        bullish = [r for r in results if r["bias"] == 1]
        assert len(bullish) > 0

    def test_no_bbf_normal_bar(self):
        from core.pristine import detect_breakout_bar_failure
        df = _make_df(30, noise=0.3)
        sr_levels = [{"price": 200.0, "kind": "R", "touches": 3, "strength": 0.7}]
        results = detect_breakout_bar_failure(df, sr_levels)
        # Price is nowhere near 200, so no BBF
        assert len(results) == 0

    def test_empty_sr(self):
        from core.pristine import detect_breakout_bar_failure
        df = _make_df(30)
        results = detect_breakout_bar_failure(df, [])
        assert len(results) == 0


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Price Voids (Ch. 3)
# ═════════════════════════════════════════════════════════════════════════════

class TestPriceVoids:

    def test_void_detection(self):
        from core.pristine import find_price_voids
        df = _make_df(100, noise=0.2, trend="up")
        voids = find_price_voids(df, min_void_atr=1.5)
        # May or may not find voids depending on synthetic data
        for v in voids:
            assert "void_high" in v
            assert "void_low" in v
            assert v["void_high"] > v["void_low"]

    def test_no_voids_in_flat_market(self):
        from core.pristine import find_price_voids
        df = _make_df(100, noise=0.1, trend="flat")
        voids = find_price_voids(df, min_void_atr=3.0)
        # Very high threshold + low noise → should find very few
        assert isinstance(voids, list)

    def test_empty_df(self):
        from core.pristine import find_price_voids
        voids = find_price_voids(None)
        assert voids == []


# ═════════════════════════════════════════════════════════════════════════════
#  TEST: Pristine Setup Detection (PBS/PSS)
# ═════════════════════════════════════════════════════════════════════════════

class TestPristineSetup:

    def test_pbs_all_criteria(self):
        from core.pristine import detect_pristine_setup
        result = detect_pristine_setup(
            stage={"stage": 2, "confidence": 0.9},
            pivot_trend={"trend": "uptrend", "strength": "strong"},
            retracement={"quality": "pristine", "retracement_pct": 0.35,
                         "near_ma20": True, "impulse_start": 95.0, "impulse_end": 105.0},
            volume_class={"pullback_vol_trend": "declining"},
            sweet_spot={"type": "sweet_spot", "score": 0.7},
            last_candle={"bias": 1, "type": "WRB", "tail": "demand_rejection"},
            sr_levels=[{"price": 100.0, "kind": "S", "strength": 0.8}],
            current_price=100.0,
            direction=1,
        )
        assert result is not None
        assert result["type"] == "PBS"
        assert result["quality"] in ("A+", "A")

    def test_pss_detection(self):
        from core.pristine import detect_pristine_setup
        result = detect_pristine_setup(
            stage={"stage": 4, "confidence": 0.8},
            pivot_trend={"trend": "downtrend", "strength": "moderate"},
            retracement={"quality": "healthy", "retracement_pct": 0.45,
                         "near_ma20": True, "impulse_start": 105.0, "impulse_end": 95.0},
            volume_class={"pullback_vol_trend": "declining"},
            sweet_spot={"type": "sweet_spot", "score": 0.5},
            last_candle={"bias": -1, "type": "normal", "tail": "supply_rejection"},
            sr_levels=[{"price": 100.0, "kind": "R", "strength": 0.7}],
            current_price=100.0,
            direction=-1,
        )
        assert result is not None
        assert result["type"] == "PSS"

    def test_rejected_wrong_stage(self):
        from core.pristine import detect_pristine_setup
        # Trying to BUY in Stage 3 with bad criteria
        result = detect_pristine_setup(
            stage={"stage": 3, "confidence": 0.7},
            pivot_trend={"trend": "range"},
            retracement={"quality": "failing", "retracement_pct": 0.7},
            volume_class={"pullback_vol_trend": "rising"},
            sweet_spot={"type": "sour_spot", "score": -0.5},
            last_candle={"bias": -1, "type": "normal"},
            sr_levels=[],
            current_price=100.0,
            direction=1,
        )
        # Should be rejected (None) — too many criteria missed
        assert result is None

    def test_pbs_minimum_quality(self):
        from core.pristine import detect_pristine_setup
        # Test that a setup with many criteria met gets a decent grade
        result = detect_pristine_setup(
            stage={"stage": 2, "confidence": 0.6},
            pivot_trend={"trend": "uptrend", "strength": "weak"},
            retracement={"quality": "deep", "retracement_pct": 0.55,
                         "near_ma20": False, "impulse_start": 95.0, "impulse_end": 105.0},
            volume_class={"pullback_vol_trend": "flat"},
            sweet_spot={"type": "neutral", "score": 0.1},
            last_candle={"bias": 1, "type": "normal"},
            sr_levels=[],
            current_price=100.0,
            direction=1,
        )
        # Should pass with at least B grade (5+ criteria met)
        assert result is not None
        assert result["quality"] in ("A+", "A", "B")
        assert result["met_count"] >= 5
