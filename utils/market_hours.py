"""
===============================================================================
  Market Hours — session detection, best-time-to-trade logic
===============================================================================
"""

from datetime import datetime, timezone
from typing import Optional

import config as cfg


def utcnow() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def _hour_in_range(hour: int, open_h: int, close_h: int) -> bool:
    """Check if *hour* (0-23) falls inside [open_h, close_h) handling midnight wrap."""
    if open_h < close_h:
        return open_h <= hour < close_h
    else:  # wraps midnight, e.g. Sydney 21-06
        return hour >= open_h or hour < close_h


def active_sessions(now: Optional[datetime] = None) -> list[str]:
    """Return list of currently active trading sessions."""
    now = now or utcnow()
    hour = now.hour
    return [
        name
        for name, hrs in cfg.SESSIONS.items()
        if _hour_in_range(hour, hrs["open"], hrs["close"])
    ]


def is_market_open(now: Optional[datetime] = None) -> bool:
    """
    Forex markets are open Sunday ~21:00 UTC → Friday ~21:00 UTC.
    Using 21:00 UTC as both open and close (winter time).
    In summer (DST) it shifts to 20:00 open / 21:00 close, but we
    use the conservative 21:00 boundary for both seasons.
    """
    now = now or utcnow()
    wd = now.weekday()  # Mon=0 … Sun=6
    hour = now.hour

    if wd == 6 and hour >= 21:    # Sunday after 21:00 UTC
        return True
    if wd == 4 and hour >= 21:    # Friday after 21:00 UTC — FIXED (was 22)
        return False
    if wd == 5:                    # Saturday
        return False
    if wd == 6 and hour < 21:     # Sunday before 21:00 UTC
        return False
    return True


def is_good_session_for_symbol(symbol: str, now: Optional[datetime] = None) -> bool:
    """Check if the current session is a good time to trade *symbol*."""
    current = set(active_sessions(now))
    if not current:
        return False

    # Extract currency codes from symbol name
    sym_upper = symbol.upper()
    for ccy, sessions in cfg.CURRENCY_SESSIONS.items():
        if ccy in sym_upper:
            if current & set(sessions):
                return True

    # If we can't determine, allow during London or NewYork (most liquid)
    return bool(current & {"London", "NewYork"})


def session_score(symbol: str, now: Optional[datetime] = None) -> float:
    """Return 0.0-1.0 score for how good the current session is for this symbol."""
    current = set(active_sessions(now))
    if not current:
        return 0.0

    sym_upper = symbol.upper()
    best_sessions: set[str] = set()
    for ccy, sessions in cfg.CURRENCY_SESSIONS.items():
        if ccy in sym_upper:
            best_sessions.update(sessions)

    if not best_sessions:
        best_sessions = {"London", "NewYork"}

    overlap = current & best_sessions
    if not overlap:
        return 0.2  # open but not ideal
    if len(overlap) >= 2:
        return 1.0  # session overlap = peak liquidity
    return 0.7
