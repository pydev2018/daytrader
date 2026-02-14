"""
===============================================================================
  Market Scanner — continuously scans the full Oanda universe
===============================================================================
  This is the tireless eye that never blinks.  It cycles through every
  tradeable symbol, runs multi-timeframe analysis, and populates the
  watchlist with qualifying setups.

  Architecture (post-watchlist redesign):
    full_scan()       — scans all symbols, populates/refreshes watchlist
    watchlist_check() — checks watchlisted symbols for M15 triggers
    
  ALL signals must go through the watchlist → trigger route.
  No signal is ever auto-executed from a scan alone.
===============================================================================
"""

from __future__ import annotations

import time
from typing import Optional

import config as cfg
from core.mt5_connector import MT5Connector
from core.confluence import analyze_symbol, SymbolAnalysis
from core.signals import TradeSignal
from core.watchlist import Watchlist
from core import news_aggregator
from utils.logger import get_logger
from utils import market_hours

log = get_logger("scanner")


class MarketScanner:
    """Scans all instruments and populates the watchlist with qualifying setups."""

    def __init__(self, mt5_conn: MT5Connector):
        self.mt5 = mt5_conn
        self._universe: list[str] = []
        self._last_scan: dict[str, SymbolAnalysis] = {}
        self._scan_count: int = 0
        self._last_event_check: float = 0
        self._active_event_window: dict | None = None
        # ── The professional stalking screen ──────────────────────────
        self._watchlist = Watchlist(self.mt5)

    def refresh_universe(self):
        """Discover all tradeable symbols from MT5."""
        self._universe = self.mt5.get_symbols_by_groups()
        log.info(f"Universe refreshed: {len(self._universe)} symbols")

    @property
    def universe(self) -> list[str]:
        return self._universe

    @property
    def watchlist(self) -> Watchlist:
        """Public access to the watchlist for diagnostics and scan-only mode."""
        return self._watchlist

    def scan_single(self, symbol: str) -> Optional[SymbolAnalysis]:
        """
        Analyze a single symbol.  Returns SymbolAnalysis or None if filtered.

        NOTE: This no longer generates signals.  The analysis is used to
        populate the watchlist.  Signals come from watchlist trigger detection.
        """
        try:
            # Quick filters before expensive analysis
            # Crypto trades 24/7; forex/CFDs need the market to be open
            if not market_hours.is_market_open(symbol=symbol):
                return None

            # Deterministic high-impact event avoidance (NFP, FOMC, ECB, etc.)
            event = news_aggregator.is_high_impact_event_window(symbol)
            if event:
                log.info(
                    f"{symbol}: SKIPPING — inside {event['event_key']} "
                    f"avoidance window ({event['phase']} by "
                    f"{abs(event['minutes_to_event']):.0f} min)"
                )
                return None

            spread = self.mt5.spread_pips(symbol)
            if spread > cfg.MAX_SPREAD_PIPS:
                return None

            # Full multi-TF analysis
            sa = analyze_symbol(self.mt5, symbol)
            self._last_scan[symbol] = sa

            return sa

        except Exception as e:
            log.error(f"Error scanning {symbol}: {e}", exc_info=True)
            return None

    def full_scan(self) -> list[TradeSignal]:
        """
        Scan the entire universe and populate the watchlist.

        IMPORTANT: Returns an EMPTY list.  All signals come through the
        watchlist → trigger route via watchlist_check().

        The return type is kept as list[TradeSignal] for backward
        compatibility with the main loop interface.
        """
        if not self._universe:
            self.refresh_universe()

        self._scan_count += 1
        start_time = time.time()

        # Filter to symbols in good sessions.
        # Crypto symbols always pass (24/7 market) even when forex is closed.
        active = market_hours.active_sessions()
        scannable = [
            sym for sym in self._universe
            if market_hours.is_good_session_for_symbol(sym)
        ]

        if not scannable:
            log.info("No symbols in active sessions — skipping scan")
            return []

        log.info(
            f"Scan #{self._scan_count}: {len(scannable)}/{len(self._universe)} symbols "
            f"(sessions: {', '.join(active) or 'none'})"
        )

        # Sequential scan (MT5 Python API is not thread-safe)
        for symbol in scannable:
            self.scan_single(symbol)

        elapsed = time.time() - start_time

        # ── Update the watchlist from scan results ────────────────────
        # This adds qualifying setups, refreshes existing entries, and
        # removes entries that no longer qualify.
        self._watchlist.update_from_scan(self._last_scan)

        # ── Scan funnel diagnostic ────────────────────────────────────
        n_with_direction = sum(
            1 for sa in self._last_scan.values()
            if sa.trade_direction is not None
        )
        n_above_50 = sum(
            1 for sa in self._last_scan.values()
            if sa.confluence_score >= 50
        )
        n_above_threshold = sum(
            1 for sa in self._last_scan.values()
            if sa.confluence_score >= cfg.WATCHLIST_SETUP_THRESHOLD
        )
        n_above_auto = sum(
            1 for sa in self._last_scan.values()
            if sa.confluence_score >= cfg.CONFIDENCE_THRESHOLD
        )

        log.info(
            f"Scan #{self._scan_count} complete in {elapsed:.1f}s │ "
            f"scanned={len(scannable)} dir={n_with_direction} "
            f">50={n_above_50} "
            f">={int(cfg.WATCHLIST_SETUP_THRESHOLD)}={n_above_threshold} "
            f">={int(cfg.CONFIDENCE_THRESHOLD)}={n_above_auto} │ "
            f"watchlist={self._watchlist.size} symbols"
        )

        # Free DataFrame memory after scan
        self._gc_scan_cache()

        # ALL signals come through watchlist triggers — return empty
        return []

    def watchlist_check(self) -> list[TradeSignal]:
        """
        Check watchlisted symbols for M15 trigger patterns.

        Called every WATCHLIST_CHECK_SECONDS between full scans.
        This is Phase 2/3 — the stalking and trigger detection.
        """
        return self._watchlist.check_triggers()

    def get_analysis(self, symbol: str) -> Optional[SymbolAnalysis]:
        """Return the latest analysis for a symbol (from cache)."""
        return self._last_scan.get(symbol)

    def _gc_scan_cache(self):
        """
        Free memory: drop stored DataFrames from TimeframeAnalysis objects.
        The DFs are only needed during signal generation and can be large.
        """
        for sym, sa in self._last_scan.items():
            for tf, tfa in sa.timeframes.items():
                tfa.df = None  # release DataFrame memory

    def top_opportunities(self, n: int = 10) -> list[dict]:
        """Return top N opportunities from the latest scan."""
        items = []
        for sym, sa in self._last_scan.items():
            if sa.trade_direction and sa.confluence_score > 50:
                items.append({
                    "symbol": sym,
                    "direction": sa.trade_direction,
                    "score": sa.confluence_score,
                    "setup_score": sa.setup_score,
                    "bias": sa.overall_bias,
                    "atr": sa.atr,
                    "spread": sa.spread_pips,
                })
        items.sort(key=lambda x: x["score"], reverse=True)
        return items[:n]
