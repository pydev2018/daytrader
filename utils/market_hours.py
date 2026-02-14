"""
===============================================================================
  Market Hours — session detection, best-time-to-trade logic
===============================================================================

Crypto symbols trade 24/7 and are exempt from:
  - Forex market open/close (Sunday 17:00 ET → Friday 17:00 ET)
  - Friday wind-down (no new trades after 12:00 ET)
  - Weekend protection (emergency close at 16:30 ET Friday)
  - Session-based symbol filtering

Crypto identification uses config.CRYPTO_PREFIXES (e.g. BTC, ETH, SOL …).
"""

from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

import config as cfg

# New York timezone — the industry-standard reference for forex market
# open/close (Sunday 17:00 ET → Friday 17:00 ET).  Using zoneinfo
# automatically handles US DST transitions.
_NY_TZ = ZoneInfo("America/New_York")


def utcnow() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


# ═════════════════════════════════════════════════════════════════════════════
#  CRYPTO DETECTION
# ═════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=256)
def is_crypto_symbol(symbol: str) -> bool:
    """
    Return True if *symbol* is a cryptocurrency instrument.

    Matches against config.CRYPTO_PREFIXES — any symbol whose name
    starts with one of these prefixes (e.g. BTCUSD, ETHJPY, SOLUSD)
    is classified as crypto.

    Results are cached for the lifetime of the process (symbol names
    don't change at runtime).
    """
    sym_upper = symbol.upper()
    # Strip broker suffixes like ".sml" for matching
    base = sym_upper.split(".")[0]
    return any(base.startswith(prefix) for prefix in cfg.CRYPTO_PREFIXES)


# ═════════════════════════════════════════════════════════════════════════════
#  SESSION HELPERS
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
#  FOREX MARKET HOURS
# ═════════════════════════════════════════════════════════════════════════════

def is_market_open(now: Optional[datetime] = None, symbol: str = "") -> bool:
    """
    Forex markets are open Sunday 17:00 ET → Friday 17:00 ET.

    **Crypto symbols are always considered "market open"** — they trade
    24/7/365 with no weekend gaps.

    Using New York time (America/New_York) as the reference clock,
    which automatically handles US DST transitions.
    """
    # Crypto trades 24/7
    if symbol and is_crypto_symbol(symbol):
        return True

    now = now or utcnow()
    # Convert to New York time for the canonical open/close check
    ny = now.astimezone(_NY_TZ)
    wd = ny.weekday()  # Mon=0 … Sun=6
    hour = ny.hour

    # Sunday 17:00 ET → market opens
    if wd == 6 and hour >= 17:
        return True
    # Friday 17:00 ET → market closes
    if wd == 4 and hour >= 17:
        return False
    # Saturday — always closed
    if wd == 5:
        return False
    # Sunday before 17:00 ET — still closed
    if wd == 6 and hour < 17:
        return False
    # Mon–Thu, or Friday before 17:00 — open
    return True


def is_new_trade_allowed(now: Optional[datetime] = None,
                         symbol: str = "") -> bool:
    """
    Whether we should open NEW trades right now.

    **Crypto symbols are always allowed** — they trade 24/7 and
    are not subject to Friday wind-down or weekend gap risk.

    For forex/CFDs:
      - Friday after 12:00 ET (noon NY) → NO new trades.
        Reason: the London/NY overlap ends, liquidity drops, and
        any H1-based Pristine entry needs 4-8+ hours to reach TP.
        A trade opened Friday afternoon will be force-closed by
        weekend protection before it can play out — a guaranteed
        forced exit at worse execution.

      - Market closed → obviously no new trades.

    Existing positions continue to be MANAGED (trail, partial, breakeven)
    through Friday close.  The weekend emergency close at ~16:30 ET is
    the absolute last resort for positions that survived the afternoon.
    """
    # Crypto trades 24/7 — no wind-down
    if symbol and is_crypto_symbol(symbol):
        return True

    if not is_market_open(now):
        return False

    now = now or utcnow()
    ny = now.astimezone(_NY_TZ)

    # Friday after noon ET → wind-down, no new entries
    if ny.weekday() == 4 and ny.hour >= 12:
        return False

    return True


def is_good_session_for_symbol(symbol: str, now: Optional[datetime] = None) -> bool:
    """
    Check if the current session is a good time to trade *symbol*.

    **Crypto symbols always return True** — they have no preferred
    session; liquidity is distributed around the clock.
    """
    # Crypto is always a good session
    if is_crypto_symbol(symbol):
        return True

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
    """
    Return 0.0-1.0 score for how good the current session is for this symbol.

    Crypto always returns 0.7 (reasonable baseline — no single session
    is definitively "best" for crypto, but volatility tends to spike
    around US/Asia hours).
    """
    if is_crypto_symbol(symbol):
        return 0.7

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
