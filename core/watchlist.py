"""
===============================================================================
  Watchlist — The Professional Stalking Screen
===============================================================================
  This module implements Phase 2 and Phase 3 of the professional trader
  workflow.  A pro trader who has made millions NEVER enters a trade based
  on structural analysis alone.  They:

    Phase 1 (Scan)    — identify structural setups across the universe
    Phase 2 (Stalk)   — monitor the best setups, waiting for entry trigger
    Phase 3 (Trigger) — recognise specific M15 candle patterns that say
                         "enter NOW"
    Phase 4 (Execute) — confirm via chart analysis, risk-adjust, execute

  ALL signals — even those scoring 90+ — must pass through this route.
  The full scan populates the watchlist.  Between scans, the watchlist
  is stalked every WATCHLIST_CHECK_SECONDS for trigger patterns on M15.

  Architecture:
    WatchlistEntry  — one symbol on the stalking screen
    Watchlist       — manages entries, detects triggers, emits signals

  CRITICAL BAR SEMANTICS (MT5):
    mt5.copy_rates_from_pos(symbol, tf, 0, count) returns position 0 =
    the CURRENT FORMING (incomplete) bar.  After chronological sort:
      iloc[-1]  = FORMING bar (incomplete — DO NOT evaluate patterns on it)
      iloc[-2]  = last CLOSED bar (confirmed — safe for pattern detection)
      iloc[-3]  = bar before that

    "New bar" detection: when iloc[-1].name (forming bar open time) changes,
    a new bar has opened, meaning iloc[-2] just closed.  This is the ONLY
    moment we evaluate triggers.

  Trigger detection checks the LAST CLOSED M15 bar for:
    1. Bullish/Bearish Engulfing
    2. Hammer / Shooting Star (ONLY at key level — EMA20/S/R)
    3. Strong Directional Close above/below prior bar
    4. Inside Bar Breakout (NRB compression → expansion)
    5. Volume Spike Reversal
    6. EMA20 Touch + Bounce / Rejection

  Each trigger is modular and returns:
    {type, strength, description, reasons, quality_gates}
  Triggers are evaluated together; the strongest qualifying trigger wins.
===============================================================================
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from core.mt5_connector import MT5Connector
from core.confluence import SymbolAnalysis
from core.signals import TradeSignal, confidence_to_win_probability
from utils.logger import get_logger
from utils import market_hours

log = get_logger("watchlist")

# Minimum trigger strength after quality gates to generate a signal.
# Below this, the trigger is logged but not acted upon.
MIN_TRIGGER_STRENGTH: float = 0.50

# M15 bar period in seconds (for stale feed detection)
_M15_SECONDS: int = 15 * 60

# Maximum tolerable age (in seconds) for the forming bar's open time.
# If the forming bar is older than this, the feed is stale.
# 2 × M15 period = 30 minutes — generous, accounts for broker lag.
_STALE_FEED_THRESHOLD: int = 2 * _M15_SECONDS

# Number of M15 bars to fetch.  50 gives good EMA20 warmup (30 bars
# past the 20-bar seed) while staying lightweight.
_TRIGGER_BAR_COUNT: int = 50


# ═════════════════════════════════════════════════════════════════════════════
#  WATCHLIST ENTRY
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class WatchlistEntry:
    """A symbol on the professional stalking screen, waiting for a trigger."""

    # ── Core identity ─────────────────────────────────────────────────────
    symbol: str
    direction: str                        # "BUY" or "SELL"
    confluence_score: float               # overall score from last scan
    setup_score: float                    # structural components only (0-70)
    score_breakdown: dict                 # per-component raw scores (0-1 each)

    # ── Trade parameters (from scan, pivot-based → stable) ────────────────
    entry_price: float                    # price at time of scan
    stop_loss: float                      # pivot-based SL
    take_profit: float                    # pivot-based TP
    atr: float
    spread_pips: float

    # ── Cached structural levels for trigger proximity checks ─────────────
    ema20: float = 0.0                    # M15 EMA20 (refreshed on trigger check)
    ema50: float = 0.0                    # M15 EMA50
    nearest_support: float = 0.0          # nearest S/R below entry
    nearest_resistance: float = 0.0       # nearest S/R above entry
    h1_sr_levels: list = field(default_factory=list)

    # ── Pristine info ─────────────────────────────────────────────────────
    pristine_setup: str = ""              # "PBS A+" etc. (cosmetic)
    sweet_spot_type: str = ""             # "sweet_spot" / "sour_spot" / ""

    # ── Timing & lifecycle ────────────────────────────────────────────────
    added_at: float = 0.0                 # time.time() when first added
    updated_at: float = 0.0              # time.time() when last refreshed by scan
    last_checked: float = 0.0            # time.time() when last checked for trigger
    last_bar_time: int = 0               # integer epoch of last FORMING bar open time
    checks: int = 0                       # number of trigger checks performed

    # ── Cached analysis reference ─────────────────────────────────────────
    # NOTE: DataFrames are already freed (set to None) by analyze_symbol().
    # Only structural metadata (stages, pivots, indicators dict) is retained.
    _analysis_ref: Optional[SymbolAnalysis] = field(default=None, repr=False)


# ═════════════════════════════════════════════════════════════════════════════
#  WATCHLIST
# ═════════════════════════════════════════════════════════════════════════════

class Watchlist:
    """
    The professional stalking screen.

    Maintains a prioritised list of high-quality setups and monitors them
    for specific M15 entry triggers between full scan cycles.

    Thread-safety: NOT thread-safe.  Called only from the main loop.
    """

    def __init__(self, mt5_conn: MT5Connector):
        self.mt5 = mt5_conn
        self._entries: dict[str, WatchlistEntry] = {}
        # Cooldown map: symbol → epoch time after which it can re-trigger.
        # Prevents rapid re-triggering on the same symbol after a signal fires.
        self._trigger_cooldowns: dict[str, float] = {}

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def symbols(self) -> list[str]:
        return list(self._entries.keys())

    def entries_sorted(self) -> list[WatchlistEntry]:
        """Return entries sorted by confluence score (highest first)."""
        return sorted(
            self._entries.values(),
            key=lambda e: e.confluence_score,
            reverse=True,
        )

    def get_entry(self, symbol: str) -> Optional[WatchlistEntry]:
        return self._entries.get(symbol)

    # ═════════════════════════════════════════════════════════════════════
    #  UPDATE FROM SCAN — called once per full scan cycle
    # ═════════════════════════════════════════════════════════════════════

    def update_from_scan(self, analyses: dict[str, SymbolAnalysis]):
        """
        Refresh the watchlist from the latest full scan results.

        - Adds new qualifying setups
        - Refreshes existing entries with fresh structural data
        - Removes entries that no longer qualify
        - Expires stale entries
        """
        now = _time.time()
        qualifying: set[str] = set()
        n_added = 0
        n_refreshed = 0

        for symbol, sa in analyses.items():
            # ── Basic qualification gates (with diagnostic logging) ────
            if sa.trade_direction is None:
                continue
            if sa.confluence_score < cfg.WATCHLIST_SETUP_THRESHOLD:
                continue

            # ── From here, the symbol passed the score threshold ───────
            # Log all candidates so we can diagnose empty watchlist issues.
            log.info(
                f"  {symbol}: CANDIDATE {sa.trade_direction} "
                f"score={sa.confluence_score:.1f} "
                f"entry={sa.entry_price:.5f} SL={sa.stop_loss:.5f} "
                f"TP={sa.take_profit:.5f} ATR={sa.atr:.5f}"
            )
            if sa.entry_price <= 0 or sa.stop_loss <= 0 or sa.take_profit <= 0:
                log.info(
                    f"  {symbol}: REJECTED (price/SL/TP zero) — "
                    f"entry={sa.entry_price:.5f} SL={sa.stop_loss:.5f} "
                    f"TP={sa.take_profit:.5f} score={sa.confluence_score:.1f}"
                )
                continue
            if sa.atr <= 0:
                log.info(f"  {symbol}: REJECTED (ATR zero) score={sa.confluence_score:.1f}")
                continue

            # SL/TP side validation
            if sa.trade_direction == "BUY":
                if sa.stop_loss >= sa.entry_price or sa.take_profit <= sa.entry_price:
                    log.info(
                        f"  {symbol}: REJECTED (SL/TP wrong side for BUY) — "
                        f"entry={sa.entry_price:.5f} SL={sa.stop_loss:.5f} "
                        f"TP={sa.take_profit:.5f} score={sa.confluence_score:.1f}"
                    )
                    continue
            else:
                if sa.stop_loss <= sa.entry_price or sa.take_profit >= sa.entry_price:
                    log.info(
                        f"  {symbol}: REJECTED (SL/TP wrong side for SELL) — "
                        f"entry={sa.entry_price:.5f} SL={sa.stop_loss:.5f} "
                        f"TP={sa.take_profit:.5f} score={sa.confluence_score:.1f}"
                    )
                    continue

            # R:R gate
            risk = abs(sa.entry_price - sa.stop_loss)
            reward = abs(sa.take_profit - sa.entry_price)
            if risk <= 0 or reward <= 0:
                log.info(
                    f"  {symbol}: REJECTED (zero risk/reward) — "
                    f"risk={risk:.5f} reward={reward:.5f} score={sa.confluence_score:.1f}"
                )
                continue
            rr = reward / risk
            if rr < cfg.MIN_RISK_REWARD_RATIO:
                log.info(
                    f"  {symbol}: REJECTED (R:R {rr:.2f} < "
                    f"{cfg.MIN_RISK_REWARD_RATIO}) — "
                    f"entry={sa.entry_price:.5f} SL={sa.stop_loss:.5f} "
                    f"TP={sa.take_profit:.5f} score={sa.confluence_score:.1f}"
                )
                continue

            qualifying.add(symbol)

            # ── Extract key levels for trigger proximity checks ────────
            ema20 = 0.0
            ema50 = 0.0
            m15_tfa = sa.timeframes.get("M15")
            if m15_tfa and m15_tfa.indicators:
                ema20 = m15_tfa.indicators.get("ema_fast", 0) or 0
                ema50 = m15_tfa.indicators.get("ema_trend", 0) or 0

            h1_sr = []
            h1_tfa = sa.timeframes.get("H1")
            if h1_tfa:
                h1_sr = h1_tfa.sr_levels or []

            # Nearest support/resistance from multi-TF or H1
            nearest_sup = 0.0
            nearest_res = 0.0
            sr_pool = sa.multi_tf_sr or (h1_tfa.sr_levels if h1_tfa else [])
            for level in sr_pool:
                p = level.get("price", 0)
                k = level.get("kind", "")
                if p > 0 and p < sa.entry_price and k in ("S", "SR"):
                    if nearest_sup == 0 or p > nearest_sup:
                        nearest_sup = p
                if p > 0 and p > sa.entry_price and k in ("R", "SR"):
                    if nearest_res == 0 or p < nearest_res:
                        nearest_res = p

            # Setup score (already computed by confluence engine)
            setup = sa.setup_score
            bd = sa.score_breakdown or {}

            # Sweet spot type
            ss_type = ""
            if sa.sweet_spot:
                ss_type = sa.sweet_spot.get("type", "")

            # ── Update existing or add new ─────────────────────────────
            if symbol in self._entries:
                entry = self._entries[symbol]
                # Check if direction changed
                if entry.direction != sa.trade_direction:
                    log.info(
                        f"  {symbol}: direction flip {entry.direction} → "
                        f"{sa.trade_direction} — resetting watchlist entry"
                    )
                    del self._entries[symbol]
                    # Fall through to create new entry below
                else:
                    # Refresh with latest data
                    entry.confluence_score = sa.confluence_score
                    entry.setup_score = setup
                    entry.score_breakdown = bd
                    entry.entry_price = sa.entry_price
                    entry.stop_loss = sa.stop_loss
                    entry.take_profit = sa.take_profit
                    entry.atr = sa.atr
                    entry.spread_pips = sa.spread_pips
                    entry.ema20 = ema20
                    entry.ema50 = ema50
                    entry.nearest_support = nearest_sup
                    entry.nearest_resistance = nearest_res
                    entry.h1_sr_levels = h1_sr
                    entry.sweet_spot_type = ss_type
                    entry.updated_at = now
                    entry._analysis_ref = sa
                    n_refreshed += 1
                    continue  # don't re-add

            # Create new entry (check capacity)
            if len(self._entries) >= cfg.WATCHLIST_MAX_ENTRIES:
                # Evict lowest-scoring entry to make room
                worst = min(
                    self._entries.values(),
                    key=lambda e: e.confluence_score,
                )
                if sa.confluence_score > worst.confluence_score:
                    log.info(
                        f"  Watchlist full — evicting {worst.symbol} "
                        f"(score={worst.confluence_score:.1f}) for {symbol} "
                        f"(score={sa.confluence_score:.1f})"
                    )
                    del self._entries[worst.symbol]
                else:
                    continue  # not good enough to replace

            entry = WatchlistEntry(
                symbol=symbol,
                direction=sa.trade_direction,
                confluence_score=sa.confluence_score,
                setup_score=setup,
                score_breakdown=bd,
                entry_price=sa.entry_price,
                stop_loss=sa.stop_loss,
                take_profit=sa.take_profit,
                atr=sa.atr,
                spread_pips=sa.spread_pips,
                ema20=ema20,
                ema50=ema50,
                nearest_support=nearest_sup,
                nearest_resistance=nearest_res,
                h1_sr_levels=h1_sr,
                sweet_spot_type=ss_type,
                added_at=now,
                updated_at=now,
                _analysis_ref=sa,
            )
            self._entries[symbol] = entry
            n_added += 1
            log.info(
                f"  {symbol}: ADDED to watchlist — "
                f"{sa.trade_direction} score={sa.confluence_score:.1f} "
                f"setup={setup:.1f} SL={sa.stop_loss:.5f} TP={sa.take_profit:.5f}"
            )

        # ── Remove entries that no longer qualify ──────────────────────
        n_removed = 0
        to_remove = [s for s in self._entries if s not in qualifying]
        for symbol in to_remove:
            entry = self._entries.pop(symbol)
            n_removed += 1
            age_min = (now - entry.added_at) / 60
            log.info(
                f"  {symbol}: REMOVED from watchlist — no longer qualifies "
                f"(was score={entry.confluence_score:.1f}, "
                f"age={age_min:.0f}min, checks={entry.checks})"
            )

        # ── Expire stale entries ──────────────────────────────────────
        n_expired = self._expire_stale()

        # ── Status log ────────────────────────────────────────────────
        entries_str = ", ".join(
            f"{e.symbol}({e.confluence_score:.0f})"
            for e in self.entries_sorted()
        )
        log.info(
            f"Watchlist: {self.size} symbols "
            f"(+{n_added} -{n_removed} ~{n_refreshed} expired={n_expired}) "
            f"│ {entries_str or '(empty)'}"
        )

    # ═════════════════════════════════════════════════════════════════════
    #  CHECK TRIGGERS — called every WATCHLIST_CHECK_SECONDS
    # ═════════════════════════════════════════════════════════════════════

    def check_triggers(self) -> list[TradeSignal]:
        """
        Check all watchlisted symbols for M15 trigger patterns.

        Called every WATCHLIST_CHECK_SECONDS (~15s) between full scans.

        CRITICAL BAR SEMANTICS:
          MT5 copy_rates_from_pos(symbol, tf, 0, count) returns position 0 =
          the FORMING (incomplete) bar.  After chronological sort:
            iloc[-1]  = FORMING bar  (incomplete — NEVER evaluate patterns)
            iloc[-2]  = last CLOSED bar  (confirmed — safe for patterns)
            iloc[-3]  = bar before that

          "New bar closed" is detected by a change in the forming bar's open
          timestamp.  When the forming bar changes, iloc[-2] just closed.
          We evaluate triggers ONLY at that moment.

        DATA QUALITY:
          - Session check: skip if market is closed
          - Stale feed: skip if forming bar open time is too old
          - Cooldown: skip symbols recently triggered
          - Bar count: require ≥ 25 closed bars (EMA20 warmup)
          - EMA is computed from CLOSED bars only (excludes forming bar)

        Returns list of TradeSignal for any triggered entries.
        """
        if not self._entries:
            return []

        signals: list[TradeSignal] = []
        triggered_symbols: list[str] = []
        now = _time.time()
        now_epoch = int(now)

        # ── Clean expired cooldowns ───────────────────────────────────
        self._trigger_cooldowns = {
            sym: ts for sym, ts in self._trigger_cooldowns.items()
            if ts > now
        }

        for symbol, entry in list(self._entries.items()):
            # ── Per-symbol session gate ────────────────────────────────
            # Crypto is always allowed (24/7).  Forex/CFDs require the
            # forex market to be open.
            if not market_hours.is_market_open(symbol=symbol):
                continue
            entry.last_checked = now

            # ── Cooldown check ────────────────────────────────────────
            if symbol in self._trigger_cooldowns:
                continue

            try:
                # ── Fetch M15 bars ────────────────────────────────────
                # 50 bars: 1 forming + 49 closed.  49 closed bars gives
                # good EMA20 warmup (29 bars past the 20-bar seed).
                m15_df = self.mt5.get_rates(symbol, "M15", count=_TRIGGER_BAR_COUNT)
                if m15_df is None or len(m15_df) < 25:
                    continue

                # ── "New bar closed" detection ────────────────────────
                # The forming bar is iloc[-1].  Its open time (index) is
                # the canonical identifier.  We use INTEGER epoch seconds
                # to avoid float-equality fragility.
                forming_bar_idx = m15_df.index[-1]
                forming_bar_epoch = int(forming_bar_idx.timestamp())

                if forming_bar_epoch == entry.last_bar_time:
                    continue  # same forming bar — nothing new to evaluate

                # ── Stale feed detection ──────────────────────────────
                # If the forming bar's open time is too old, the feed is
                # stale (broker disconnect, market closed, etc.).
                bar_age_seconds = now_epoch - forming_bar_epoch
                if bar_age_seconds > _STALE_FEED_THRESHOLD:
                    if entry.checks % 20 == 0:  # log sparingly
                        log.debug(
                            f"{symbol}: stale M15 feed — forming bar "
                            f"age {bar_age_seconds}s (threshold "
                            f"{_STALE_FEED_THRESHOLD}s)"
                        )
                    continue

                # ── Commit: this is a new, valid forming bar ──────────
                entry.last_bar_time = forming_bar_epoch
                entry.checks += 1

                # ── Separate CLOSED bars from the forming bar ─────────
                # NEVER evaluate patterns on the forming bar.
                closed_bars = m15_df.iloc[:-1]
                if len(closed_bars) < 22:
                    continue  # need ≥ 22 closed bars for EMA20 + 2 bars

                # ── Refresh EMA20 from CLOSED bars only ───────────────
                # ewm on closed bars avoids look-ahead from the forming
                # bar.  50 - 1 = 49 closed bars → 29 bars past seed.
                ema20_series = closed_bars["close"].ewm(
                    span=20, adjust=False,
                ).mean()
                entry.ema20 = float(ema20_series.iloc[-1])

                # ── Extract the last CLOSED bar and its predecessor ───
                closed_bar = closed_bars.iloc[-1]
                prev_bar = closed_bars.iloc[-2]

                # ── Detect trigger patterns on CLOSED bars ────────────
                trigger = self._detect_trigger(
                    entry, closed_bar, prev_bar, closed_bars,
                )
                if trigger is None:
                    continue

                # ── Trigger fired!  Create a TradeSignal. ─────────────
                signal = self._create_signal_from_trigger(entry, trigger)
                if signal is None:
                    continue

                signals.append(signal)
                triggered_symbols.append(symbol)

                # ── Cooldown: prevent re-triggering for 2 × M15 bars ─
                self._trigger_cooldowns[symbol] = now + 2 * _M15_SECONDS

                age_min = (now - entry.added_at) / 60
                log.info(
                    f"TRIGGER FIRED: {entry.direction} {symbol} — "
                    f"{trigger['type']} (strength={trigger['strength']:.2f}) — "
                    f"\"{trigger['description']}\" — "
                    f"reasons={trigger.get('reasons', [])} — "
                    f"on watchlist for {age_min:.0f}min, "
                    f"{entry.checks} checks"
                )

            except Exception as e:
                log.error(
                    f"Watchlist trigger check error for {symbol}: {e}",
                    exc_info=True,
                )

        # ── Remove triggered symbols from watchlist ───────────────────
        for symbol in triggered_symbols:
            self._entries.pop(symbol, None)

        return signals

    # ═════════════════════════════════════════════════════════════════════
    #  TRIGGER DETECTION ENGINE
    # ═════════════════════════════════════════════════════════════════════

    def _detect_trigger(
        self,
        entry: WatchlistEntry,
        closed_bar: pd.Series,
        prev_bar: pd.Series,
        closed_bars: pd.DataFrame,
    ) -> Optional[dict]:
        """
        Detect specific candle-based trigger patterns on the LAST CLOSED
        M15 bar.

        CRITICAL: ``closed_bar`` is iloc[-2] of the raw MT5 data (i.e.,
        the last CONFIRMED bar).  The forming bar (iloc[-1]) is EXCLUDED.
        This prevents repainting — patterns evaluated here are final.

        Parameters
        ----------
        closed_bar : pd.Series
            The last CLOSED M15 bar (OHLCV confirmed).
        prev_bar : pd.Series
            The bar before ``closed_bar``.
        closed_bars : pd.DataFrame
            All closed bars (excluding the forming bar) for context
            calculations (averages, EMA, etc.).

        Returns
        -------
        dict or None
            {type, strength, description, reasons, quality_gates}
            Returns None if no qualifying trigger fires.

        DESIGN NOTE: Each pattern is a separate block.  To add a new
        trigger, add a block at the end of the BUY / SELL sections.
        """
        direction = 1 if entry.direction == "BUY" else -1

        # ── Bar data (all from CLOSED bars — confirmed) ──────────────
        c_open = float(closed_bar["open"])
        c_close = float(closed_bar["close"])
        c_high = float(closed_bar["high"])
        c_low = float(closed_bar["low"])

        p_open = float(prev_bar["open"])
        p_close = float(prev_bar["close"])
        p_high = float(prev_bar["high"])
        p_low = float(prev_bar["low"])

        c_body = abs(c_close - c_open)
        p_body = abs(p_close - p_open)
        c_range = c_high - c_low
        p_range = p_high - p_low

        if c_range <= 0 or p_range <= 0:
            return None

        c_is_bull = c_close > c_open
        c_is_bear = c_close < c_open

        # ── Context (averages from CLOSED bars, last 10) ─────────────
        bodies = (closed_bars["close"] - closed_bars["open"]).abs()
        avg_body = float(bodies.iloc[-10:].mean())
        avg_range = float(
            (closed_bars["high"] - closed_bars["low"]).iloc[-10:].mean()
        )

        # ── Volume handling ──────────────────────────────────────────
        # get_rates() renames tick_volume → "volume".
        # For FX/CFDs tick volume is the only proxy for activity.
        # We label it explicitly and gate detectors that rely on it.
        vol_col = "volume" if "volume" in closed_bars.columns else None
        if vol_col is None and "real_volume" in closed_bars.columns:
            vol_col = "real_volume"
        volume_available = vol_col is not None

        c_vol = float(closed_bar[vol_col]) if volume_available else 0.0
        avg_vol = (
            float(closed_bars[vol_col].iloc[-10:].mean())
            if volume_available else 0.0
        )
        vol_ratio = c_vol / avg_vol if avg_vol > 0 else 1.0
        # Flag: is volume data trustworthy for this symbol?
        vol_trusted = volume_available and avg_vol > 0

        if avg_body <= 0:
            avg_body = c_body or c_range * 0.5

        # ── Quality gates ─────────────────────────────────────────────
        # Each gate produces a multiplier (0.0 – 1.0).  Product of all
        # multipliers scales the raw trigger strength.  If any gate is
        # 0.0, the trigger is suppressed.
        quality_mult = 1.0
        quality_reasons: list[str] = []

        # Gate: spread sanity (cached from scan — may be stale)
        if entry.spread_pips > cfg.MAX_SPREAD_PIPS * 0.8:
            spread_factor = max(
                0.3, 1.0 - (entry.spread_pips / cfg.MAX_SPREAD_PIPS - 0.8)
            )
            quality_mult *= spread_factor
            quality_reasons.append(
                f"SPREAD_WIDE({entry.spread_pips:.1f}p)"
            )

        # Gate: ATR sanity
        if entry.atr <= 0:
            return None

        triggers: list[dict] = []

        # ═════════════════════════════════════════════════════════════
        #  BUY TRIGGERS
        # ═════════════════════════════════════════════════════════════
        if direction == 1:

            # ── 1. Bullish Engulfing ─────────────────────────────────
            # Current closed bullish bar's body completely engulfs
            # prior bar.  Classic reversal/continuation at support.
            if (c_is_bull
                    and c_close > p_high
                    and c_open <= p_low
                    and c_body > p_body * 1.2):
                strength = min(
                    1.0, 0.70 + (c_body / avg_body - 1) * 0.15
                )
                reasons = ["BODY_ENGULFS_PRIOR"]
                if vol_trusted and vol_ratio > 1.3:
                    strength += 0.05
                    reasons.append(f"VOL_CONFIRM({vol_ratio:.1f}x)")
                triggers.append({
                    "type": "bullish_engulfing",
                    "strength": min(1.0, strength),
                    "description": (
                        f"Bullish engulfing: body {c_body:.5f} engulfs "
                        f"prior {p_body:.5f}"
                    ),
                    "reasons": reasons,
                })

            # ── 2. Hammer at support / EMA20 ─────────────────────────
            # Long lower wick (≥2x body), tiny upper wick.
            # REQUIRES confluence with a key level (EMA20 or S/R).
            # Without level, the pattern is meaningless noise.
            lower_wick = min(c_open, c_close) - c_low
            upper_wick = c_high - max(c_open, c_close)
            if (c_body > 0
                    and lower_wick > c_body * 2
                    and upper_wick < c_body * 0.5):
                at_level = False
                level_name = ""
                if (entry.ema20 > 0
                        and abs(c_low - entry.ema20) < entry.atr * 0.3):
                    at_level = True
                    level_name = "EMA20"
                elif (entry.nearest_support > 0
                        and abs(c_low - entry.nearest_support)
                        < entry.atr * 0.5):
                    at_level = True
                    level_name = f"support@{entry.nearest_support:.5f}"

                # GATE: hammer ONLY fires at a key level
                if at_level:
                    strength = 0.70 + (lower_wick / c_range) * 0.2
                    reasons = [
                        "WICK_REJECTION",
                        f"AT_LEVEL({level_name})",
                    ]
                    triggers.append({
                        "type": "hammer",
                        "strength": min(1.0, strength),
                        "description": (
                            f"Hammer at {level_name}: "
                            f"wick={lower_wick:.5f} body={c_body:.5f}"
                        ),
                        "reasons": reasons,
                    })

            # ── 3. Strong Close Above Prior High ─────────────────────
            # Bullish bar closes above the previous bar's high with
            # decent body.  Indicates buyers in control.
            if (c_is_bull
                    and c_close > p_high
                    and c_body > avg_body * 0.8):
                strength = 0.60 + min(
                    0.30, (c_close - p_high) / entry.atr * 2
                )
                reasons = ["CLOSE_THROUGH_HIGH"]
                if vol_trusted and vol_ratio > 1.3:
                    strength += 0.10
                    reasons.append(f"VOL_CONFIRM({vol_ratio:.1f}x)")
                triggers.append({
                    "type": "strong_close_above",
                    "strength": min(1.0, strength),
                    "description": (
                        f"Close {c_close:.5f} > prior high "
                        f"{p_high:.5f} (body={c_body:.5f})"
                    ),
                    "reasons": reasons,
                })

            # ── 4. Inside Bar Breakout ───────────────────────────────
            # Previous bar was a Narrow Range Bar (compression).
            # Current bar breaks out above its high.
            # Compression → expansion = energy release.
            if (p_range < avg_range * 0.6
                    and c_close > p_high
                    and c_is_bull):
                strength = 0.70
                reasons = ["NRB_COMPRESSION", "BREAKOUT"]
                if vol_trusted and vol_ratio > 1.2:
                    strength += min(0.20, (vol_ratio - 1.0) * 0.15)
                    reasons.append(f"VOL_EXPAND({vol_ratio:.1f}x)")
                triggers.append({
                    "type": "inside_bar_breakout",
                    "strength": min(1.0, strength),
                    "description": (
                        f"Inside bar breakout: NRB "
                        f"({p_range / avg_range:.0%} of avg), "
                        f"close={c_close:.5f} > {p_high:.5f}"
                    ),
                    "reasons": reasons,
                })

            # ── 5. Volume Spike Reversal ─────────────────────────────
            # Prior bar was bearish, current bar bullish with volume
            # spike (>1.5x avg).  Institutional reversal.
            # GATE: requires trusted volume data.
            p_is_bear = p_close < p_open
            if (vol_trusted
                    and c_is_bull
                    and p_is_bear
                    and vol_ratio > 1.5
                    and c_close > p_open):
                strength = min(1.0, 0.65 + (vol_ratio - 1.5) * 0.10)
                triggers.append({
                    "type": "volume_spike_reversal",
                    "strength": strength,
                    "description": (
                        f"Volume spike ({vol_ratio:.1f}x avg) "
                        f"with bullish reversal"
                    ),
                    "reasons": [
                        f"RVOL({vol_ratio:.1f}x)",
                        "DIRECTION_FLIP",
                    ],
                })

            # ── 6. EMA20 Touch + Bounce ──────────────────────────────
            # Price touched or wicked through EMA20 and closed above.
            # Classic Pristine pullback entry (Ch. 6).
            if (entry.ema20 > 0
                    and c_low <= entry.ema20 * 1.002
                    and c_close > entry.ema20
                    and c_is_bull
                    and c_body > avg_body * 0.5):
                triggers.append({
                    "type": "ema20_bounce",
                    "strength": 0.75,
                    "description": (
                        f"EMA20 bounce: low {c_low:.5f} tested "
                        f"EMA20 {entry.ema20:.5f}, closed above "
                        f"at {c_close:.5f}"
                    ),
                    "reasons": ["EMA20_TEST", "CLOSED_ABOVE"],
                })

        # ═════════════════════════════════════════════════════════════
        #  SELL TRIGGERS  (mirror of BUY patterns)
        # ═════════════════════════════════════════════════════════════
        else:

            # ── 1. Bearish Engulfing ─────────────────────────────────
            if (c_is_bear
                    and c_close < p_low
                    and c_open >= p_high
                    and c_body > p_body * 1.2):
                strength = min(
                    1.0, 0.70 + (c_body / avg_body - 1) * 0.15
                )
                reasons = ["BODY_ENGULFS_PRIOR"]
                if vol_trusted and vol_ratio > 1.3:
                    strength += 0.05
                    reasons.append(f"VOL_CONFIRM({vol_ratio:.1f}x)")
                triggers.append({
                    "type": "bearish_engulfing",
                    "strength": min(1.0, strength),
                    "description": (
                        f"Bearish engulfing: body {c_body:.5f} engulfs "
                        f"prior {p_body:.5f}"
                    ),
                    "reasons": reasons,
                })

            # ── 2. Shooting Star at resistance / EMA20 ───────────────
            # REQUIRES confluence with a key level.
            upper_wick = c_high - max(c_open, c_close)
            lower_wick = min(c_open, c_close) - c_low
            if (c_body > 0
                    and upper_wick > c_body * 2
                    and lower_wick < c_body * 0.5):
                at_level = False
                level_name = ""
                if (entry.ema20 > 0
                        and abs(c_high - entry.ema20) < entry.atr * 0.3):
                    at_level = True
                    level_name = "EMA20"
                elif (entry.nearest_resistance > 0
                        and abs(c_high - entry.nearest_resistance)
                        < entry.atr * 0.5):
                    at_level = True
                    level_name = f"resistance@{entry.nearest_resistance:.5f}"

                # GATE: shooting star ONLY fires at a key level
                if at_level:
                    strength = 0.70 + (upper_wick / c_range) * 0.2
                    triggers.append({
                        "type": "shooting_star",
                        "strength": min(1.0, strength),
                        "description": (
                            f"Shooting star at {level_name}: "
                            f"wick={upper_wick:.5f} body={c_body:.5f}"
                        ),
                        "reasons": [
                            "WICK_REJECTION",
                            f"AT_LEVEL({level_name})",
                        ],
                    })

            # ── 3. Strong Close Below Prior Low ──────────────────────
            if (c_is_bear
                    and c_close < p_low
                    and c_body > avg_body * 0.8):
                strength = 0.60 + min(
                    0.30, (p_low - c_close) / entry.atr * 2
                )
                reasons = ["CLOSE_THROUGH_LOW"]
                if vol_trusted and vol_ratio > 1.3:
                    strength += 0.10
                    reasons.append(f"VOL_CONFIRM({vol_ratio:.1f}x)")
                triggers.append({
                    "type": "strong_close_below",
                    "strength": min(1.0, strength),
                    "description": (
                        f"Close {c_close:.5f} < prior low "
                        f"{p_low:.5f} (body={c_body:.5f})"
                    ),
                    "reasons": reasons,
                })

            # ── 4. Inside Bar Breakdown ──────────────────────────────
            if (p_range < avg_range * 0.6
                    and c_close < p_low
                    and c_is_bear):
                strength = 0.70
                reasons = ["NRB_COMPRESSION", "BREAKDOWN"]
                if vol_trusted and vol_ratio > 1.2:
                    strength += min(0.20, (vol_ratio - 1.0) * 0.15)
                    reasons.append(f"VOL_EXPAND({vol_ratio:.1f}x)")
                triggers.append({
                    "type": "inside_bar_breakdown",
                    "strength": min(1.0, strength),
                    "description": (
                        f"Inside bar breakdown: NRB "
                        f"({p_range / avg_range:.0%} of avg), "
                        f"close={c_close:.5f} < {p_low:.5f}"
                    ),
                    "reasons": reasons,
                })

            # ── 5. Volume Spike Reversal (bearish) ───────────────────
            # GATE: requires trusted volume data.
            p_is_bull = p_close > p_open
            if (vol_trusted
                    and c_is_bear
                    and p_is_bull
                    and vol_ratio > 1.5
                    and c_close < p_open):
                strength = min(1.0, 0.65 + (vol_ratio - 1.5) * 0.10)
                triggers.append({
                    "type": "volume_spike_reversal",
                    "strength": strength,
                    "description": (
                        f"Volume spike ({vol_ratio:.1f}x avg) "
                        f"with bearish reversal"
                    ),
                    "reasons": [
                        f"RVOL({vol_ratio:.1f}x)",
                        "DIRECTION_FLIP",
                    ],
                })

            # ── 6. EMA20 Touch + Rejection ───────────────────────────
            if (entry.ema20 > 0
                    and c_high >= entry.ema20 * 0.998
                    and c_close < entry.ema20
                    and c_is_bear
                    and c_body > avg_body * 0.5):
                triggers.append({
                    "type": "ema20_rejection",
                    "strength": 0.75,
                    "description": (
                        f"EMA20 rejection: high {c_high:.5f} tested "
                        f"EMA20 {entry.ema20:.5f}, closed below "
                        f"at {c_close:.5f}"
                    ),
                    "reasons": ["EMA20_TEST", "CLOSED_BELOW"],
                })

        # ═════════════════════════════════════════════════════════════
        #  QUALITY-GATE SCALING + STRENGTH FLOOR
        # ═════════════════════════════════════════════════════════════
        if not triggers:
            return None

        # Apply quality multiplier to all trigger strengths
        for t in triggers:
            t["strength"] *= quality_mult
            t["quality_gates"] = quality_reasons.copy()

        # Filter by minimum strength
        viable = [t for t in triggers if t["strength"] >= MIN_TRIGGER_STRENGTH]
        if not viable:
            # Log the suppressed triggers for diagnostics
            best_raw = max(triggers, key=lambda t: t["strength"])
            log.debug(
                f"{entry.symbol}: trigger {best_raw['type']} suppressed — "
                f"strength {best_raw['strength']:.2f} < "
                f"{MIN_TRIGGER_STRENGTH} after quality gates "
                f"(gates={quality_reasons})"
            )
            return None

        best = max(viable, key=lambda t: t["strength"])

        # Diagnostic: log all detected triggers
        if len(viable) > 1:
            others = [
                f"{t['type']}({t['strength']:.2f})"
                for t in viable if t is not best
            ]
            log.debug(
                f"{entry.symbol}: {len(viable)} triggers — "
                f"best={best['type']}({best['strength']:.2f}), "
                f"also: {', '.join(others)}"
            )

        return best

    # ═════════════════════════════════════════════════════════════════════
    #  SIGNAL CREATION FROM TRIGGER
    # ═════════════════════════════════════════════════════════════════════

    def _create_signal_from_trigger(
        self,
        entry: WatchlistEntry,
        trigger: dict,
    ) -> Optional[TradeSignal]:
        """
        Create a validated TradeSignal from a triggered watchlist entry.

        Uses fresh tick price for entry, cached SL/TP (pivot-based,
        structurally stable between scans).  Validates R:R and spread
        with current market conditions.
        """
        # ── Fresh entry price from live tick ──────────────────────────
        tick = self.mt5.symbol_tick(entry.symbol)
        if tick is None:
            log.warning(f"{entry.symbol}: no tick for signal creation")
            return None

        fresh_entry = tick["ask"] if entry.direction == "BUY" else tick["bid"]

        # ── Fresh spread check ────────────────────────────────────────
        fresh_spread = self.mt5.spread_pips(entry.symbol)
        if fresh_spread > cfg.MAX_SPREAD_PIPS:
            log.info(
                f"{entry.symbol}: trigger fired but spread too wide "
                f"({fresh_spread:.1f} > {cfg.MAX_SPREAD_PIPS})"
            )
            return None

        # ── Use cached SL/TP (pivot-based, stable) ────────────────────
        sl = entry.stop_loss
        tp = entry.take_profit

        # Validate SL/TP with fresh entry price
        if entry.direction == "BUY":
            if sl >= fresh_entry or tp <= fresh_entry:
                log.debug(
                    f"{entry.symbol}: SL/TP invalid with fresh price "
                    f"(entry={fresh_entry:.5f} SL={sl:.5f} TP={tp:.5f})"
                )
                return None
        else:
            if sl <= fresh_entry or tp >= fresh_entry:
                log.debug(
                    f"{entry.symbol}: SL/TP invalid with fresh price "
                    f"(entry={fresh_entry:.5f} SL={sl:.5f} TP={tp:.5f})"
                )
                return None

        # ── R:R with fresh price ──────────────────────────────────────
        risk = abs(fresh_entry - sl)
        reward = abs(tp - fresh_entry)
        if risk <= 0 or reward <= 0:
            return None
        rr_ratio = reward / risk
        if rr_ratio < cfg.MIN_RISK_REWARD_RATIO:
            log.debug(
                f"{entry.symbol}: trigger ok but R:R {rr_ratio:.2f} "
                f"< {cfg.MIN_RISK_REWARD_RATIO} with fresh price"
            )
            return None

        # ── Risk tier (same tiers as before) ──────────────────────────
        score = entry.confluence_score
        if score >= cfg.CONFIDENCE_THRESHOLD:
            tier_risk_factor = cfg.RISK_FACTOR_AUTO_ACCEPT
            review_band = False
        elif score >= 65:
            tier_risk_factor = cfg.RISK_FACTOR_STANDARD_REVIEW
            review_band = True
        else:
            tier_risk_factor = cfg.RISK_FACTOR_DEEP_REVIEW
            review_band = True

        win_prob = confidence_to_win_probability(score)

        # ── Build rationale ───────────────────────────────────────────
        age_min = (_time.time() - entry.added_at) / 60
        rationale = [
            f"WATCHLIST TRIGGER: {trigger['type']} — {trigger['description']}",
            f"Setup: score={entry.confluence_score:.1f} "
            f"(structural={entry.setup_score:.1f}/70)",
            f"Stalked for {age_min:.0f}min, {entry.checks} trigger checks",
        ]

        # Structural rationale from cached analysis
        sa = entry._analysis_ref
        if sa:
            for tf in ("D1", "H4"):
                tfa = sa.timeframes.get(tf)
                if tfa and tfa.stage:
                    st = tfa.stage
                    rationale.append(
                        f"{tf} Stage {st.get('stage', '?')} "
                        f"({st.get('description', '?')}) "
                        f"[conf={st.get('confidence', 0):.0%}]"
                    )
                    break
            if sa.sweet_spot:
                ss = sa.sweet_spot
                rationale.append(
                    f"Multi-TF: {ss.get('type', '?')} "
                    f"(score={ss.get('score', 0):.2f})"
                )
            # H1 pivot trend
            h1 = sa.timeframes.get("H1")
            if h1 and h1.pivot_trend:
                pv = h1.pivot_trend
                rationale.append(
                    f"H1 pivot trend: {pv.get('trend', '?')} "
                    f"({pv.get('strength', '?')})"
                )
            # Retracement
            if h1 and h1.retracement:
                ret = h1.retracement
                rationale.append(
                    f"H1 retracement: {ret.get('quality', '?')} "
                    f"({ret.get('retracement_pct', 0):.0%})"
                )

        # ── Create signal ─────────────────────────────────────────────
        signal = TradeSignal(
            symbol=entry.symbol,
            direction=entry.direction,
            entry_price=fresh_entry,
            stop_loss=sl,
            take_profit=tp,
            confidence=score,
            win_probability=win_prob,
            risk_reward_ratio=round(rr_ratio, 2),
            atr=entry.atr,
            spread_pips=fresh_spread,
            rationale=rationale,
            pristine_setup=entry.pristine_setup,
            review_band=review_band,
            risk_factor=tier_risk_factor,
        )

        log.info(
            f"SIGNAL from watchlist: {signal.direction} {signal.symbol} "
            f"@ {signal.entry_price:.5f}  SL={signal.stop_loss:.5f}  "
            f"TP={signal.take_profit:.5f}  R:R={signal.risk_reward_ratio}  "
            f"Conf={signal.confidence:.1f}  WinP={signal.win_probability:.2f}  "
            f"RiskF={signal.risk_factor:.2f}  "
            f"Trigger={trigger['type']}"
        )

        return signal

    # ═════════════════════════════════════════════════════════════════════
    #  EXPIRY
    # ═════════════════════════════════════════════════════════════════════

    def _expire_stale(self) -> int:
        """Remove entries older than WATCHLIST_MAX_AGE_HOURS.  Returns count."""
        now = _time.time()
        max_age = cfg.WATCHLIST_MAX_AGE_HOURS * 3600

        stale = [
            symbol for symbol, entry in self._entries.items()
            if now - entry.added_at > max_age
        ]
        for symbol in stale:
            entry = self._entries.pop(symbol)
            age_h = (now - entry.added_at) / 3600
            log.info(
                f"{symbol}: EXPIRED from watchlist — "
                f"age={age_h:.1f}h, checks={entry.checks}, "
                f"no trigger fired"
            )
        return len(stale)
