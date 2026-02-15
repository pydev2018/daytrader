"""
Fill simulation helpers.
"""

from __future__ import annotations


def can_fill_limit(side: str, price: float, bar: dict) -> bool:
    if side == "BUY":
        return bar["ask_low"] <= price
    return bar["bid_high"] >= price


def apply_slippage(side: str, price: float, tick_size: float, slip_ticks: int) -> float:
    if side == "BUY":
        return price + slip_ticks * tick_size
    return price - slip_ticks * tick_size
