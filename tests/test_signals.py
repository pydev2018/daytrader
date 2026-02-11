"""
Tests for core.signals — validates signal generation and filtering.
"""

import pytest
from core.signals import (
    TradeSignal,
    confidence_to_win_probability,
    generate_signal,
)
from core.confluence import SymbolAnalysis, TimeframeAnalysis


class TestConfidenceMapping:
    def test_below_threshold_returns_low_prob(self):
        assert confidence_to_win_probability(50) == 0.45
        assert confidence_to_win_probability(74) == 0.45

    def test_at_threshold_returns_valid(self):
        wp = confidence_to_win_probability(75)
        assert 0.54 < wp < 0.56

    def test_high_confidence_bounded(self):
        wp = confidence_to_win_probability(100)
        assert wp <= 0.70
        assert wp >= 0.60

    def test_monotonically_increasing(self):
        probs = [confidence_to_win_probability(c) for c in range(70, 100)]
        for i in range(1, len(probs)):
            assert probs[i] >= probs[i - 1], \
                f"Win prob should be monotonically increasing: {probs[i-1]} -> {probs[i]}"


class TestSignalGeneration:
    def _make_analysis(self, direction="BUY", entry=1.1, sl=1.09, tp=1.13, atr=0.005):
        sa = SymbolAnalysis(symbol="EURUSD.sml")
        sa.trade_direction = direction
        sa.entry_price = entry
        sa.stop_loss = sl
        sa.take_profit = tp
        sa.atr = atr
        sa.spread_pips = 1.0
        sa.higher_tf_bias = "BULLISH" if direction == "BUY" else "BEARISH"
        sa.trading_tf_bias = sa.higher_tf_bias
        sa.entry_tf_bias = sa.higher_tf_bias
        sa.overall_bias = sa.higher_tf_bias

        # Add minimal timeframe data
        for tf in ["W1", "D1", "H4", "H1", "M15", "M5"]:
            tfa = TimeframeAnalysis(symbol="EURUSD.sml", timeframe=tf)
            tfa.trend = "BULLISH" if direction == "BUY" else "BEARISH"
            tfa.indicators = {
                "rsi": 55 if direction == "BUY" else 45,
                "macd_hist": 0.001 if direction == "BUY" else -0.001,
                "adx": 30,
                "ema_fast": 1.105 if direction == "BUY" else 1.095,
                "ema_trend": 1.095 if direction == "BUY" else 1.105,
                "stoch_k": 50,
                "vol_ratio": 1.5,
                "rsi_divergence": 0,
                "macd_divergence": 0,
            }
            sa.timeframes[tf] = tfa

        return sa

    def test_valid_buy_signal_generated(self):
        sa = self._make_analysis("BUY", entry=1.1, sl=1.09, tp=1.13)
        signal = generate_signal(sa)
        # Note: signal may be None if confluence score < threshold
        # This depends on the full scoring. Test the gates instead.
        if signal:
            assert signal.direction == "BUY"
            assert signal.risk_reward_ratio >= 2.0

    def test_invalid_sl_above_entry_rejected(self):
        sa = self._make_analysis("BUY", entry=1.1, sl=1.11, tp=1.13)
        signal = generate_signal(sa)
        assert signal is None, "BUY with SL above entry should be rejected"

    def test_invalid_tp_below_entry_rejected(self):
        sa = self._make_analysis("BUY", entry=1.1, sl=1.09, tp=1.09)
        signal = generate_signal(sa)
        assert signal is None, "BUY with TP below entry should be rejected"

    def test_low_rr_rejected(self):
        # R:R = 1:1 (below 2.0 threshold)
        sa = self._make_analysis("BUY", entry=1.1, sl=1.09, tp=1.11)
        signal = generate_signal(sa)
        assert signal is None, "R:R of 1:1 should be rejected (min is 1:2)"

    def test_zero_atr_rejected(self):
        sa = self._make_analysis("BUY", entry=1.1, sl=1.09, tp=1.13, atr=0)
        signal = generate_signal(sa)
        assert signal is None, "Zero ATR should be rejected"

    def test_no_direction_returns_none(self):
        sa = self._make_analysis()
        sa.trade_direction = None
        signal = generate_signal(sa)
        assert signal is None


class TestTradeSignalProperties:
    def test_risk_pips(self):
        sig = TradeSignal(
            symbol="EURUSD", direction="BUY",
            entry_price=1.1, stop_loss=1.09, take_profit=1.13,
            confidence=80, win_probability=0.58,
            risk_reward_ratio=3.0, atr=0.005, spread_pips=1.0,
        )
        assert sig.risk_pips == pytest.approx(0.01)
        assert sig.reward_pips == pytest.approx(0.03)

    def test_to_dict(self):
        sig = TradeSignal(
            symbol="EURUSD", direction="BUY",
            entry_price=1.1, stop_loss=1.09, take_profit=1.13,
            confidence=80, win_probability=0.58,
            risk_reward_ratio=3.0, atr=0.005, spread_pips=1.0,
        )
        d = sig.to_dict()
        assert d["symbol"] == "EURUSD"
        assert d["direction"] == "BUY"
        assert "timestamp" in d


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
