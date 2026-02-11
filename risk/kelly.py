"""
===============================================================================
  Kelly Criterion — Mathematically optimal position sizing
===============================================================================
  f* = (bp - q) / b
  where:
    b = average win / average loss  (win-loss ratio)
    p = probability of winning
    q = 1 - p  (probability of losing)

  We use HALF-Kelly (cfg.KELLY_FRACTION = 0.5) for safety.
  This ensures we never over-bet, even if our estimates are slightly off.
===============================================================================
"""

from __future__ import annotations

import config as cfg
from utils.logger import get_logger

log = get_logger("kelly")


def kelly_fraction(
    win_probability: float,
    win_loss_ratio: float,
) -> float:
    """
    Compute the Kelly fraction (optimal bet size as fraction of capital).

    Parameters
    ----------
    win_probability : float
        Estimated probability of winning (0-1).
    win_loss_ratio : float
        Average win / average loss (e.g. 2.0 means wins are 2x losses).

    Returns
    -------
    float
        Fraction of capital to risk (0 to ~0.25, capped for safety).
    """
    if win_loss_ratio <= 0 or win_probability <= 0 or win_probability >= 1:
        return 0.0

    b = win_loss_ratio
    p = win_probability
    q = 1 - p

    f_star = (b * p - q) / b

    # If Kelly is negative, the edge doesn't exist → don't bet
    if f_star <= 0:
        log.debug(
            f"Kelly negative ({f_star:.4f}): p={p:.3f} b={b:.2f} — no edge"
        )
        return 0.0

    # Apply fractional Kelly for safety
    f_adjusted = f_star * cfg.KELLY_FRACTION

    # Hard cap: never risk more than MAX_RISK_PER_TRADE_PCT_CAP
    cap = cfg.MAX_RISK_PER_TRADE_PCT_CAP / 100
    f_final = min(f_adjusted, cap)

    log.debug(
        f"Kelly: p={p:.3f} b={b:.2f} → f*={f_star:.4f} "
        f"→ half-Kelly={f_adjusted:.4f} → capped={f_final:.4f}"
    )

    return f_final


def kelly_from_confidence(
    confidence: float,
    risk_reward_ratio: float,
) -> float:
    """
    Convenience: compute Kelly fraction from our confidence score and R:R.

    Maps confidence to win probability, uses R:R as the win/loss ratio.
    """
    from core.signals import confidence_to_win_probability
    win_prob = confidence_to_win_probability(confidence)
    return kelly_fraction(win_prob, risk_reward_ratio)
