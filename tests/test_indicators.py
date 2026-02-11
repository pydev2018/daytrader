"""
Tests for core.indicators — validates indicator correctness after audit fixes.
"""

import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    rng = np.random.RandomState(seed)
    close = 1.1000 + np.cumsum(rng.randn(n) * 0.001)
    high = close + rng.uniform(0.0005, 0.002, n)
    low = close - rng.uniform(0.0005, 0.002, n)
    open_ = close + rng.randn(n) * 0.0005
    volume = rng.randint(100, 5000, n).astype(float)
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class TestStochastic:
    def test_slow_stochastic_smoothed(self):
        """Verify the Stochastic %K is smoothed (slow stochastic)."""
        from core.indicators import add_stochastic
        df = _make_ohlcv(100)
        df = add_stochastic(df)
        # With smoothing, %K should be smoother than raw %K
        assert "stoch_k" in df.columns
        assert "stoch_d" in df.columns
        # Should be bounded [0, 100] (excluding NaN warmup)
        valid = df["stoch_k"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_stochastic_d_is_average_of_k(self):
        from core.indicators import add_stochastic
        import config as cfg
        df = _make_ohlcv(100)
        df = add_stochastic(df)
        # %D should be the SMA of %K
        manual_d = df["stoch_k"].rolling(window=cfg.STOCH_D).mean()
        pd.testing.assert_series_equal(df["stoch_d"], manual_d, check_names=False)


class TestCCI:
    def test_cci_no_apply(self):
        """Verify CCI produces valid output with the vectorised implementation."""
        from core.indicators import add_cci
        df = _make_ohlcv(100)
        df = add_cci(df)
        assert "cci" in df.columns
        valid = df["cci"].dropna()
        assert len(valid) > 50
        # CCI should swing positive and negative
        assert valid.min() < 0 < valid.max()


class TestVWAP:
    def test_vwap_resets_daily(self):
        """VWAP must reset at each day boundary."""
        from core.indicators import add_vwap
        # Create 2 days of M15 data
        idx = pd.date_range("2025-01-01 00:00", periods=192, freq="15min", tz="UTC")
        n = len(idx)
        rng = np.random.RandomState(1)
        df = pd.DataFrame({
            "high": 100 + rng.rand(n),
            "low": 99 + rng.rand(n),
            "close": 99.5 + rng.rand(n),
            "volume": rng.randint(100, 1000, n).astype(float),
        }, index=idx)
        df = add_vwap(df)

        # First bar of each day should be tp (since cumvol = vol, cumtpvol = tp*vol)
        day2_start = df[df.index.date == df.index.date[96]]
        first_bar = day2_start.iloc[0]
        expected_vwap = (first_bar["high"] + first_bar["low"] + first_bar["close"]) / 3
        assert abs(first_bar["vwap"] - expected_vwap) < 1e-6, \
            f"VWAP should reset at day boundary: got {first_bar['vwap']}, expected {expected_vwap}"


class TestDivergence:
    def test_bullish_divergence_detected(self):
        """Create clear bullish divergence and verify detection."""
        from core.indicators import detect_divergence
        n = 60
        # Price makes lower lows, RSI makes higher lows
        price = pd.Series(np.concatenate([
            np.linspace(50, 40, 20),  # drop
            np.linspace(40, 45, 10),  # bounce
            np.linspace(45, 38, 20),  # lower low
            np.linspace(38, 42, 10),  # bounce
        ]))
        indicator = pd.Series(np.concatenate([
            np.linspace(50, 30, 20),  # RSI drops
            np.linspace(30, 40, 10),  # bounce
            np.linspace(40, 35, 20),  # RSI higher low (35 > 30)
            np.linspace(35, 42, 10),  # bounce
        ]))
        result = detect_divergence(price, indicator, lookback=30, swing_window=3)
        # Should detect at least one bullish divergence
        assert (result == 1).any(), "Should detect bullish divergence"

    def test_no_divergence_on_random(self):
        """Random walk should produce few divergences."""
        from core.indicators import detect_divergence
        rng = np.random.RandomState(42)
        price = pd.Series(100 + np.cumsum(rng.randn(200) * 0.01))
        indicator = pd.Series(50 + np.cumsum(rng.randn(200) * 0.1))
        result = detect_divergence(price, indicator)
        # Should not flood with false signals
        div_rate = (result != 0).sum() / len(result)
        assert div_rate < 0.15, f"Too many divergences on random data: {div_rate:.1%}"


class TestComputeAll:
    def test_compute_all_runs(self):
        """Full indicator suite should run without errors."""
        from core.indicators import compute_all_indicators
        df = _make_ohlcv(300)
        result = compute_all_indicators(df)
        assert result is not None
        assert len(result) == 300
        # Check key indicators exist
        for col in ["rsi", "macd", "atr", "adx", "stoch_k", "cci", "vwap", "obv"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_compute_all_on_short_df(self):
        """Should handle short DataFrames gracefully."""
        from core.indicators import compute_all_indicators
        df = _make_ohlcv(10)
        result = compute_all_indicators(df)
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
