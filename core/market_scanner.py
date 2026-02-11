"""
===============================================================================
  Market Scanner — continuously scans the full Oanda universe
===============================================================================
  This is the tireless eye that never blinks.  It cycles through every
  tradeable symbol, runs multi-timeframe analysis, and emits signals
  for anything that passes the confidence threshold.
===============================================================================
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import config as cfg
from core.mt5_connector import MT5Connector
from core.confluence import analyze_symbol, SymbolAnalysis
from core.signals import generate_signal, TradeSignal
from utils.logger import get_logger
from utils import market_hours

log = get_logger("scanner")


class MarketScanner:
    """Scans all instruments and produces trade signals."""

    def __init__(self, mt5_conn: MT5Connector):
        self.mt5 = mt5_conn
        self._universe: list[str] = []
        self._last_scan: dict[str, SymbolAnalysis] = {}
        self._scan_count: int = 0

    def refresh_universe(self):
        """Discover all tradeable symbols from MT5."""
        self._universe = self.mt5.get_symbols_by_groups()
        log.info(f"Universe refreshed: {len(self._universe)} symbols")

    @property
    def universe(self) -> list[str]:
        return self._universe

    def scan_single(self, symbol: str) -> Optional[TradeSignal]:
        """
        Analyze a single symbol and return a signal if one qualifies.
        """
        try:
            # Quick filters before expensive analysis
            if not market_hours.is_market_open():
                return None

            spread = self.mt5.spread_pips(symbol)
            if spread > cfg.MAX_SPREAD_PIPS:
                return None

            # Full multi-TF analysis
            sa = analyze_symbol(self.mt5, symbol)
            self._last_scan[symbol] = sa

            # Generate signal if the setup qualifies
            signal = generate_signal(sa)
            return signal

        except Exception as e:
            log.error(f"Error scanning {symbol}: {e}", exc_info=True)
            return None

    def full_scan(self, max_workers: int = 4) -> list[TradeSignal]:
        """
        Scan the entire universe and return all qualifying signals.
        Uses threading for parallelism since MT5 calls are I/O-bound.
        """
        if not self._universe:
            self.refresh_universe()

        if not market_hours.is_market_open():
            log.info("Market closed — skipping scan")
            return []

        self._scan_count += 1
        start_time = time.time()
        signals: list[TradeSignal] = []

        # Filter to symbols in good sessions
        active = market_hours.active_sessions()
        scannable = [
            sym for sym in self._universe
            if market_hours.is_good_session_for_symbol(sym)
        ]

        log.info(
            f"Scan #{self._scan_count}: {len(scannable)}/{len(self._universe)} symbols "
            f"(sessions: {', '.join(active) or 'none'})"
        )

        # Sequential scan to avoid MT5 thread issues
        # (MT5 Python API is not thread-safe, so we scan sequentially)
        for symbol in scannable:
            signal = self.scan_single(symbol)
            if signal is not None:
                signals.append(signal)

        elapsed = time.time() - start_time

        # Sort by confidence (best first)
        signals.sort(key=lambda s: s.confidence, reverse=True)

        log.info(
            f"Scan #{self._scan_count} complete: "
            f"{len(signals)} signals from {len(scannable)} symbols "
            f"in {elapsed:.1f}s"
        )

        if signals:
            for sig in signals[:5]:  # log top 5
                log.info(
                    f"  → {sig.direction:4s} {sig.symbol:12s} "
                    f"conf={sig.confidence:5.1f}  R:R=1:{sig.risk_reward_ratio:.1f}  "
                    f"winP={sig.win_probability:.0%}"
                )

        return signals

    def get_analysis(self, symbol: str) -> Optional[SymbolAnalysis]:
        """Return the latest analysis for a symbol (from cache)."""
        return self._last_scan.get(symbol)

    def top_opportunities(self, n: int = 10) -> list[dict]:
        """Return top N opportunities from the latest scan."""
        items = []
        for sym, sa in self._last_scan.items():
            if sa.trade_direction and sa.confluence_score > 50:
                items.append({
                    "symbol": sym,
                    "direction": sa.trade_direction,
                    "score": sa.confluence_score,
                    "bias": sa.overall_bias,
                    "atr": sa.atr,
                    "spread": sa.spread_pips,
                })
        items.sort(key=lambda x: x["score"], reverse=True)
        return items[:n]
