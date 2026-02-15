"""
Tests for backtest/fills.py — fill simulation helpers.
"""

import pytest

from backtest.fills import can_fill_limit, apply_slippage


class TestCanFillLimit:
    def test_buy_fill_at_ask_low(self):
        """BUY limit fills when ask_low <= price."""
        bar = {"ask_low": 1.09990, "bid_high": 1.10010}
        assert can_fill_limit("BUY", 1.09990, bar) is True
        assert can_fill_limit("BUY", 1.10000, bar) is True
        assert can_fill_limit("BUY", 1.09980, bar) is False

    def test_sell_fill_at_bid_high(self):
        """SELL limit fills when bid_high >= price."""
        bar = {"ask_low": 1.09990, "bid_high": 1.10010}
        assert can_fill_limit("SELL", 1.10010, bar) is True
        assert can_fill_limit("SELL", 1.10000, bar) is True
        assert can_fill_limit("SELL", 1.10020, bar) is False


class TestApplySlippage:
    def test_buy_slippage_adverse(self):
        """BUY slippage should make fill price worse (higher)."""
        fill = apply_slippage("BUY", 1.10000, 0.00001, 2)
        assert fill == 1.10000 + 2 * 0.00001

    def test_sell_slippage_adverse(self):
        """SELL slippage should make fill price worse (lower)."""
        fill = apply_slippage("SELL", 1.10000, 0.00001, 2)
        assert fill == 1.10000 - 2 * 0.00001

    def test_zero_slippage(self):
        assert apply_slippage("BUY", 1.10000, 0.00001, 0) == 1.10000
        assert apply_slippage("SELL", 1.10000, 0.00001, 0) == 1.10000
