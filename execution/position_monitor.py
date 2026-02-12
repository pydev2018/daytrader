"""
===============================================================================
  Position Monitor — Pristine Method Trade Management  (Ch. 7, 12, 13)
===============================================================================
  Manages open positions using the full Pristine methodology:

  • Multi-TF awareness:
      H1 (trading TF)  — bar-by-bar health, pivot structure, trailing
      M15 (entry TF)   — bar-by-bar confirmation
      D1  (macro TF)   — stage change detection, S/R proximity

  • Pristine decision hierarchy:
      1. Emergency close  — macro stage reversed, BBF + macro S/R
      2. Aggressive tighten — bar-by-bar exit signal, BBF against
      3. Structural partial — approaching macro resistance (Ch. 12)
      4. Warning response  — bar-by-bar warning → tighten to structure
      5. Profitable mgmt   — breakeven, structure-based trailing
      6. Mechanical fallback — R-based partial TP

  • Key Pristine principles applied:
      "Only use bar-by-bar analysis on the relevant time frame" (Ch. 7)
      "Sell half when the macro meets resistance" (Ch. 12)
      "A BBF spells trouble for all time frames" (Ch. 13)
      "Use the failure of the smaller TF to confirm the larger TF" (Ch. 12)

  Design: Pristine checks layer ON TOP of mechanical safety rules.
  The mechanical rules (breakeven at 1R, trail, partial at 2R) serve as
  the baseline safety net.  Pristine checks can trigger earlier and
  smarter actions based on price structure.
===============================================================================
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone, timedelta
import numpy as np

import MetaTrader5 as mt5
import config as cfg
from core.mt5_connector import MT5Connector
from core.indicators import add_atr
from core.pristine import (
    bar_by_bar_assessment,
    find_pivots,
    classify_pivots_major_minor,
    determine_trend_from_pivots,
    classify_stage,
    detect_breakout_bar_failure,
)
from core.structures import find_sr_levels
from execution.trade_executor import TradeExecutor
from risk.risk_manager import RiskManager
from alerts.telegram import TelegramAlerter
from utils.logger import get_logger
from utils import market_hours

log = get_logger("pos_monitor")


class PositionMonitor:
    """Monitors and manages all open positions using the Pristine Method."""

    def __init__(
        self,
        mt5_conn: MT5Connector,
        executor: TradeExecutor,
        risk_mgr: RiskManager,
        alerter: TelegramAlerter,
    ):
        self.mt5 = mt5_conn
        self.executor = executor
        self.risk = risk_mgr
        self.alerter = alerter
        # Track which positions have been partially closed
        self._partial_closed: set[int] = set()
        # Track which positions have been moved to breakeven
        self._breakeven_set: set[int] = set()
        # Pristine trade contexts — macro state per open position
        self._trade_contexts: dict[int, dict] = {}

    # ═════════════════════════════════════════════════════════════════════════
    #  PUBLIC INTERFACE
    # ═════════════════════════════════════════════════════════════════════════

    def check_all_positions(self):
        """
        Full bar-close analysis — called once per SCAN_INTERVAL_SECONDS.
        Runs the complete Pristine assessment: multi-TF bar fetches,
        bar-by-bar, pivots, BBF, macro stage, structural trailing.
        """
        positions = self.mt5.our_positions()
        if not positions:
            return

        for pos in positions:
            try:
                self._manage_position(pos)
            except Exception as e:
                log.error(f"Error managing position {pos.get('ticket', '?')}: {e}")

    def fast_check_all_positions(self):
        """
        Fast tick surveillance — called every TICK_CHECK_SECONDS between
        full analysis cycles.

        Uses ONLY symbol_info_tick (reads from local MT5 memory, near-zero
        cost) and cached thresholds from the last full Pristine analysis.
        NO bar fetches, NO indicator computation, NO pivot analysis.

        Catches two things the 60s cycle would miss:
          1. Price entering macro S/R partial-close zone  → sell half
          2. Rapid adverse move (> 1.5 ATR since last analysis) → tighten
        """
        positions = self.mt5.our_positions()
        if not positions:
            return

        for pos in positions:
            try:
                self._fast_tick_check(pos)
            except Exception as e:
                log.error(f"Fast tick error #{pos.get('ticket', '?')}: {e}")

    # ═════════════════════════════════════════════════════════════════════════
    #  FAST TICK SURVEILLANCE (Tier 1 — every 5-10s)
    # ═════════════════════════════════════════════════════════════════════════

    def _fast_tick_check(self, pos: dict):
        """
        Lightweight tick-level check using ONLY cached thresholds.

        Cost: 1 × symbol_info_tick per position (local memory read).
        No bar fetches, no DataFrames, no indicator math.
        """
        ticket = pos.get("ticket", 0)
        symbol = pos.get("symbol", "")
        ctx = self._trade_contexts.get(ticket)
        if not ctx:
            return  # no context yet — wait for first full cycle

        direction = ctx["direction"]
        cached_atr = ctx.get("cached_atr", 0)
        if cached_atr <= 0:
            return  # full cycle hasn't run yet

        tick = self.mt5.symbol_tick(symbol)
        if tick is None:
            return
        current_price = tick["bid"] if direction == 1 else tick["ask"]

        # ── 1. Macro S/R partial close zone ──────────────────────────────
        # If price has entered the zone around D1 resistance (BUY) or
        # support (SELL), trigger partial close immediately rather than
        # waiting up to 60s for the next full cycle.
        adverse_sr = ctx.get("nearest_adverse_sr", 0)
        if adverse_sr > 0 and ticket not in self._partial_closed:
            distance = abs(current_price - adverse_sr)
            if distance <= cached_atr * cfg.MACRO_SR_PARTIAL_ATR:
                # Verify we're in profit before partialing
                entry_price = ctx.get("entry_price", 0)
                original_sl = ctx.get("original_sl", 0)
                risk_dist = (abs(entry_price - original_sl) if original_sl
                             else cached_atr * cfg.ATR_SL_MULTIPLIER)
                if risk_dist > 0:
                    current_r = ((current_price - entry_price) / risk_dist
                                 if direction == 1
                                 else (entry_price - current_price) / risk_dist)
                    if current_r >= 0.5:
                        vol = pos.get("volume", 0)
                        sym_info = self.mt5.symbol_info(symbol)
                        vol_min = sym_info.get("volume_min", 0.01) if sym_info else 0.01
                        if vol * 0.5 >= vol_min:
                            result = self.executor.close_position(
                                pos, reason="tick_macro_sr_partial", partial=0.5
                            )
                            if result:
                                self._partial_closed.add(ticket)
                                pnl = result.get("pnl", 0)
                                log.info(
                                    f"{symbol} #{ticket}: FAST TICK partial — "
                                    f"macro S/R zone at {current_r:.1f}R "
                                    f"(S/R={adverse_sr:.5f})"
                                )
                                self.alerter.custom(
                                    f"<b>FAST TICK PARTIAL</b>\n"
                                    f"{symbol} #{ticket}\n"
                                    f"Price entered D1 S/R zone "
                                    f"({distance/cached_atr:.1f} ATR away)\n"
                                    f"Closed 50% at {current_r:.1f}R\n"
                                    f"P/L: ${pnl:,.2f}"
                                )
                                return  # don't also tighten in same tick

        # ── 2. Rapid adverse move detection ──────────────────────────────
        # If price moved > 1.5 ATR against us since the last full analysis,
        # something dramatic happened (news spike, flash crash).  Tighten
        # immediately — don't wait 60s for the next bar-close cycle.
        last_analysis_price = ctx.get("last_analysis_price", current_price)
        if direction == 1:
            adverse_move = last_analysis_price - current_price
        else:
            adverse_move = current_price - last_analysis_price

        if adverse_move > cached_atr * 1.5:
            current_sl = pos.get("sl", 0)
            new_sl = self._emergency_tighten(
                pos, direction, current_price, cached_atr,
                current_sl, ticket, symbol,
                f"FAST TICK rapid adverse move "
                f"({adverse_move / cached_atr:.1f} ATR)"
            )
            if new_sl != current_sl:
                # Update cached SL so we don't re-trigger every 5s
                ctx["last_analysis_price"] = current_price

        # ── 3. Detect closed-by-SL/TP between full cycles ───────────────
        # (handled by handle_closed_positions in the next full cycle;
        #  the fast loop only detects and acts on live positions)

    # ═════════════════════════════════════════════════════════════════════════
    #  CORE: PRISTINE POSITION MANAGEMENT  (Ch. 7, 12, 13)
    # ═════════════════════════════════════════════════════════════════════════

    def _manage_position(self, pos: dict):
        """
        Apply Pristine Method management rules to a single position.

        Decision hierarchy (prioritised):
          1. Emergency close  — macro stage reversed, BBF at macro S/R
          2. Aggressive tighten — bar-by-bar "exit", BBF against
          3. Structural partial — approaching macro resistance  (Ch. 12)
          4. Warning response  — tighten stop to structure
          5. Profitable management — breakeven + structure-based trailing
          6. Mechanical partial TP fallback
        """
        ticket = pos.get("ticket", 0)
        symbol = pos.get("symbol", "")
        pos_type = pos.get("type", 0)
        direction = 1 if pos_type == mt5.ORDER_TYPE_BUY else -1
        open_price = pos.get("price_open", 0)
        volume = pos.get("volume", 0)
        sl = pos.get("sl", 0)
        tp = pos.get("tp", 0)

        # ── Current price ────────────────────────────────────────────────
        tick = self.mt5.symbol_tick(symbol)
        if tick is None:
            return
        current_price = tick["bid"] if direction == 1 else tick["ask"]

        # ── Symbol info ──────────────────────────────────────────────────
        sym_info = self.mt5.symbol_info(symbol)
        vol_min = sym_info.get("volume_min", 0.01) if sym_info else 0.01
        spread_buffer = 0
        if sym_info:
            spread_buffer = sym_info.get("spread", 0) * sym_info.get("point", 0.00001) * 2

        # ── Fetch multi-TF data ──────────────────────────────────────────
        h1_df = self.mt5.get_rates(symbol, "H1", count=100)
        m15_df = self.mt5.get_rates(symbol, "M15", count=100)

        if h1_df is None or len(h1_df) < 30:
            return  # cannot manage without H1 data
        h1_df = add_atr(h1_df)
        current_atr = h1_df["atr"].iloc[-1]
        if np.isnan(current_atr) or current_atr == 0:
            return

        # ── Trade context (macro state, refreshed periodically) ──────────
        ctx = self._ensure_trade_context(
            ticket, symbol, direction, open_price, sl, tp, h1_df
        )

        # ── R-multiple ───────────────────────────────────────────────────
        risk_distance = abs(open_price - sl) if sl != 0 else current_atr * cfg.ATR_SL_MULTIPLIER
        if risk_distance == 0:
            return
        current_r = ((current_price - open_price) / risk_distance if direction == 1
                     else (open_price - current_price) / risk_distance)
        ctx["peak_r"] = max(ctx.get("peak_r", current_r), current_r)

        # ═════════════════════════════════════════════════════════════════
        #  PRISTINE HEALTH ASSESSMENT  (Ch. 7, 12, 13)
        # ═════════════════════════════════════════════════════════════════

        # 1. Macro stage change (Ch. 1, 12)
        macro_action = self._check_macro_stage(ctx, direction)

        # 2. Macro S/R proximity (Ch. 12 — "sell half at resistance")
        sr_action = self._check_macro_sr_proximity(
            ctx, current_price, direction, current_atr
        )

        # 3. BBF against position (Ch. 13)
        bbf_action = self._check_bbf_against(h1_df, ctx, direction)

        # 4. H1 bar-by-bar (Ch. 7 — trading TF)
        try:
            h1_health = bar_by_bar_assessment(h1_df, direction)
        except Exception:
            h1_health = {"health": "ok"}

        # 5. M15 bar-by-bar (Ch. 7 — entry TF, supplementary)
        m15_health = {"health": "ok"}
        if m15_df is not None and len(m15_df) >= 20:
            try:
                m15_health = bar_by_bar_assessment(m15_df, direction)
            except Exception:
                pass

        # 6. H1 pivot trend integrity (Ch. 10)
        trend_action = self._check_trend_integrity(h1_df, direction)

        # ═════════════════════════════════════════════════════════════════
        #  DECISION ENGINE — Pristine-first, mechanical fallback
        # ═════════════════════════════════════════════════════════════════

        # ── Priority 1: Emergency close ──────────────────────────────────
        # Macro stage fully reversed → thesis is dead
        if macro_action == "close":
            log.warning(
                f"{symbol} #{ticket}: MACRO STAGE REVERSED "
                f"(stage={ctx.get('macro_stage')}) — closing position"
            )
            close_result = self.executor.close_position(
                pos, reason="macro_stage_reversed"
            )
            if close_result:
                self.alerter.custom(
                    f"<b>PRISTINE EXIT</b>\n"
                    f"{symbol} #{ticket} — Macro stage reversed\n"
                    f"P/L: ${pos.get('profit', 0):+,.2f}"
                )
                return
            # Close failed — fall through to tighten/trail instead of returning
            log.error(
                f"{symbol} #{ticket}: FAILED to close on macro reversal — "
                "will tighten and retry next cycle"
            )

        # BBF against + approaching macro S/R = extremely dangerous
        if bbf_action == "tighten" and sr_action == "partial":
            log.warning(
                f"{symbol} #{ticket}: BBF against position + approaching "
                f"macro S/R — closing position"
            )
            close_result = self.executor.close_position(
                pos, reason="bbf_at_macro_sr"
            )
            if close_result:
                self.alerter.custom(
                    f"<b>PRISTINE EXIT</b>\n"
                    f"{symbol} #{ticket} — BBF at macro S/R\n"
                    f"P/L: ${pos.get('profit', 0):+,.2f}"
                )
                return
            log.error(
                f"{symbol} #{ticket}: FAILED to close on BBF at macro S/R — "
                "will tighten and retry next cycle"
            )

        # ── Priority 2: Aggressive tighten ───────────────────────────────
        # BBF against position (without macro S/R) → tighten aggressively
        if bbf_action == "tighten":
            sl = self._emergency_tighten(
                pos, direction, current_price, current_atr,
                sl, ticket, symbol, "BBF against position"
            )

        # H1 bar-by-bar "exit" signal → tighten
        if h1_health.get("health") == "exit":
            reasons = ", ".join(h1_health.get("reasons", []))
            sl = self._emergency_tighten(
                pos, direction, current_price, current_atr,
                sl, ticket, symbol, f"H1 bar-by-bar EXIT ({reasons})"
            )

        # M15 "exit" ONLY if H1 also shows weakness (Ch. 7: don't
        # over-manage by watching a smaller TF in isolation)
        if (m15_health.get("health") == "exit"
                and h1_health.get("health") in ("warning", "exit")):
            sl = self._emergency_tighten(
                pos, direction, current_price, current_atr,
                sl, ticket, symbol, "M15+H1 combined exit signal"
            )

        # ── Priority 3: Structural partial close (Ch. 12) ────────────────
        # "Sell half when the macro meets resistance"
        if (sr_action == "partial"
                and ticket not in self._partial_closed
                and current_r >= 0.5):
            can_split = volume * 0.5 >= vol_min
            if can_split:
                close_result = self.executor.close_position(
                    pos, reason="macro_sr_partial", partial=0.5
                )
                if close_result:
                    self._partial_closed.add(ticket)
                    pnl = close_result.get("pnl", 0)
                    log.info(
                        f"{symbol} #{ticket}: PRISTINE partial close — "
                        f"approaching macro S/R at {current_r:.1f}R"
                    )
                    self.alerter.custom(
                        f"<b>PRISTINE PARTIAL</b>\n"
                        f"{symbol} #{ticket} — approaching macro S/R\n"
                        f"Closed 50% at {current_r:.1f}R\n"
                        f"P/L on partial: ${pnl:,.2f}"
                    )

        # ── Priority 4: Warning — tighten to structure ───────────────────
        # Macro warning, H1 trend weakening, or bar-by-bar warning
        if (macro_action == "warn"
                or trend_action in ("broken", "weakening")
                or h1_health.get("health") == "warning"):
            struct_sl = self._compute_structure_sl(
                h1_df, direction, current_price, open_price, current_atr
            )
            if struct_sl > 0:
                if direction == 1 and struct_sl > sl:
                    if self.executor.modify_sl_tp(pos, new_sl=struct_sl):
                        reason = (
                            "macro_warn" if macro_action == "warn"
                            else ("trend_" + str(trend_action)
                                  if trend_action else "h1_bar_warning")
                        )
                        log.info(
                            f"{symbol} #{ticket}: {reason} — "
                            f"SL tightened to structure {struct_sl:.5f}"
                        )
                        sl = struct_sl
                elif direction == -1 and (sl == 0 or struct_sl < sl):
                    if self.executor.modify_sl_tp(pos, new_sl=struct_sl):
                        sl = struct_sl

            # If no structure available, try breakeven as fallback
            elif ticket not in self._breakeven_set:
                sl = self._apply_breakeven(
                    pos, direction, open_price, sl, spread_buffer,
                    ticket, symbol
                )

        # ── Priority 5: Profitable trade management ──────────────────────

        # 5a. Breakeven at TRAILING_STOP_ACTIVATE_R (default 1.0R)
        if (ticket not in self._breakeven_set
                and current_r >= cfg.TRAILING_STOP_ACTIVATE_R):
            sl = self._apply_breakeven(
                pos, direction, open_price, sl, spread_buffer,
                ticket, symbol
            )

        # 5b. Structure-based trailing (Ch. 7 — "line in the sand")
        #     Uses H1 pivot lows/highs as stop references, with ATR fallback
        if (ticket in self._breakeven_set
                and current_r >= cfg.TRAILING_STOP_ACTIVATE_R + 0.5):

            # Structure-based: most recent H1 pivot as stop reference
            struct_sl = self._compute_structure_sl(
                h1_df, direction, current_price, open_price, current_atr
            )
            # ATR-based fallback
            trail_distance = (current_atr * cfg.TRAILING_STOP_DISTANCE_ATR
                              + spread_buffer)

            if direction == 1:
                atr_trail_sl = current_price - trail_distance
                # Use the TIGHTER of structure vs ATR (both must be valid)
                new_trail_sl = (max(struct_sl, atr_trail_sl) if struct_sl > 0
                                else atr_trail_sl)
                if new_trail_sl > sl and new_trail_sl > open_price:
                    if self.executor.modify_sl_tp(pos, new_sl=new_trail_sl):
                        trail_type = "structure" if struct_sl > atr_trail_sl else "ATR"
                        log.debug(
                            f"{symbol} #{ticket}: trail SL ({trail_type}) "
                            f"→ {new_trail_sl:.5f}"
                        )
                        sl = new_trail_sl
            else:
                atr_trail_sl = current_price + trail_distance
                new_trail_sl = (min(struct_sl, atr_trail_sl) if struct_sl > 0
                                else atr_trail_sl)
                if (sl == 0 or new_trail_sl < sl) and new_trail_sl < open_price:
                    if self.executor.modify_sl_tp(pos, new_sl=new_trail_sl):
                        sl = new_trail_sl

        # ── Priority 6: Mechanical partial TP (fallback) ─────────────────
        # Only if structural partial hasn't already triggered
        can_split = (volume * cfg.PARTIAL_TP_RATIO >= vol_min
                     and volume * (1 - cfg.PARTIAL_TP_RATIO) >= vol_min)
        if (ticket not in self._partial_closed
                and current_r >= cfg.PARTIAL_TP_RR
                and can_split):
            close_result = self.executor.close_position(
                pos, reason="partial_tp", partial=cfg.PARTIAL_TP_RATIO
            )
            if close_result:
                self._partial_closed.add(ticket)
                pnl = close_result.get("pnl", 0)
                self.alerter.custom(
                    f"<b>PARTIAL TP</b>\n"
                    f"{symbol} — closed {cfg.PARTIAL_TP_RATIO:.0%} "
                    f"at {current_r:.1f}R\n"
                    f"P/L: ${pnl:,.2f}"
                )

        # ── Cache thresholds for fast tick surveillance ──────────────────
        # These are read by _fast_tick_check() between full analysis cycles.
        # Only symbol_info_tick is called in the fast path — no bar fetches.
        ctx["last_analysis_price"] = current_price
        ctx["cached_atr"] = current_atr
        ctx["cached_sl"] = sl

        # Pre-compute nearest adverse S/R for the fast tick loop.
        # BUY → nearest resistance above;  SELL → nearest support below.
        nearest_adverse = 0.0
        for level in ctx.get("macro_sr_levels", []):
            lvl_price = level.get("price", 0)
            kind = level.get("kind", "")
            if direction == 1 and lvl_price > current_price and kind in ("R", "SR"):
                if nearest_adverse == 0 or lvl_price < nearest_adverse:
                    nearest_adverse = lvl_price
            elif direction == -1 and lvl_price < current_price and kind in ("S", "SR"):
                if nearest_adverse == 0 or lvl_price > nearest_adverse:
                    nearest_adverse = lvl_price
        ctx["nearest_adverse_sr"] = nearest_adverse

    # ═════════════════════════════════════════════════════════════════════════
    #  TRADE CONTEXT — macro state per position
    # ═════════════════════════════════════════════════════════════════════════

    def _ensure_trade_context(
        self, ticket: int, symbol: str, direction: int,
        open_price: float, sl: float, tp: float,
        h1_df,
    ) -> dict:
        """Create or return the trade context for a position."""
        if ticket in self._trade_contexts:
            ctx = self._trade_contexts[ticket]
            # Refresh macro data if stale
            if _time.time() - ctx.get("macro_refresh_ts", 0) > cfg.MACRO_REFRESH_SECONDS:
                self._refresh_macro_context(ctx, symbol, direction, h1_df)
            return ctx

        # First encounter — capture full context
        ctx = {
            "ticket": ticket,
            "symbol": symbol,
            "direction": direction,
            "entry_price": open_price,
            "original_sl": sl,
            "original_tp": tp,
            "peak_r": 0.0,
            # Macro state (populated by _refresh_macro_context)
            "macro_stage": 0,
            "macro_stage_direction": None,
            "macro_sr_levels": [],
            "h1_sr_levels": [],
            "macro_refresh_ts": 0.0,
        }
        self._refresh_macro_context(ctx, symbol, direction, h1_df)
        self._trade_contexts[ticket] = ctx
        log.info(
            f"{symbol} #{ticket}: trade context created — "
            f"D1 stage={ctx['macro_stage']}, "
            f"macro S/R levels={len(ctx['macro_sr_levels'])}"
        )
        return ctx

    def _refresh_macro_context(
        self, ctx: dict, symbol: str, direction: int, h1_df,
    ):
        """Fetch D1 data and update the macro context for a position."""
        # ── D1 data for stage and S/R ────────────────────────────────────
        d1_df = self.mt5.get_rates(symbol, "D1", count=252)
        if d1_df is not None and len(d1_df) >= 50:
            d1_df = add_atr(d1_df)
            # Stage classification (Ch. 1)
            d1_pivots = find_pivots(d1_df)
            d1_pivots = classify_pivots_major_minor(d1_pivots)
            d1_pivot_trend = determine_trend_from_pivots(d1_pivots)
            d1_stage = classify_stage(d1_df, pivot_trend=d1_pivot_trend)

            ctx["macro_stage"] = d1_stage.get("stage", 0)
            ctx["macro_stage_direction"] = d1_stage.get("allowed_direction")
            ctx["macro_stage_confidence"] = d1_stage.get("confidence", 0)

            # D1 S/R levels (Ch. 3)
            ctx["macro_sr_levels"] = find_sr_levels(d1_df)

        # ── H1 S/R for BBF detection and structure stops ─────────────────
        if h1_df is not None and len(h1_df) >= 30:
            ctx["h1_sr_levels"] = find_sr_levels(h1_df)

        ctx["macro_refresh_ts"] = _time.time()

    # ═════════════════════════════════════════════════════════════════════════
    #  PRISTINE HEALTH CHECKS
    # ═════════════════════════════════════════════════════════════════════════

    def _check_macro_stage(self, ctx: dict, direction: int) -> str | None:
        """
        Check if the D1 stage has changed against our position (Ch. 1, 12).

        If we entered during Stage 2 (BUY) and D1 transitions to Stage 4,
        the macro thesis is dead.  Stage 3 (distribution) is a warning.

        Returns: "close" | "warn" | None
        """
        stage = ctx.get("macro_stage", 0)

        if direction == 1:  # BUY
            if stage == 4:
                return "close"
            if stage == 3:
                return "warn"
        else:  # SELL
            if stage == 2:
                return "close"
            if stage == 1:
                return "warn"
        return None

    def _check_macro_sr_proximity(
        self, ctx: dict, current_price: float,
        direction: int, current_atr: float,
    ) -> str | None:
        """
        Check if price is approaching macro (D1) S/R against our direction
        (Ch. 12).

        "When the macro uptrend meets some kind of resistance...
         sell half of the position and keep half."

        Returns: "partial" | None
        """
        macro_sr = ctx.get("macro_sr_levels", [])
        if not macro_sr or current_atr <= 0:
            return None

        partial_distance = current_atr * cfg.MACRO_SR_PARTIAL_ATR

        for level in macro_sr[:10]:
            lvl_price = level.get("price", 0)
            kind = level.get("kind", "")
            distance = abs(current_price - lvl_price)

            # BUY approaching resistance
            if (direction == 1
                    and lvl_price > current_price
                    and kind in ("R", "SR")
                    and distance <= partial_distance):
                log.debug(
                    f"{ctx['symbol']}: within {distance/current_atr:.1f} ATR "
                    f"of D1 resistance at {lvl_price:.5f}"
                )
                return "partial"

            # SELL approaching support
            if (direction == -1
                    and lvl_price < current_price
                    and kind in ("S", "SR")
                    and distance <= partial_distance):
                log.debug(
                    f"{ctx['symbol']}: within {distance/current_atr:.1f} ATR "
                    f"of D1 support at {lvl_price:.5f}"
                )
                return "partial"

        return None

    def _check_bbf_against(
        self, h1_df, ctx: dict, direction: int,
    ) -> str | None:
        """
        Check for Breakout Bar Failure against our position (Ch. 13).

        "A BBF can be used to indicate trouble, not only for the current
        time frame, but also for the larger time frame."

        Returns: "tighten" | None
        """
        # Use H1 S/R for detection (fresh each cycle) + macro levels
        sr_levels = ctx.get("h1_sr_levels", [])
        if not sr_levels:
            sr_levels = ctx.get("macro_sr_levels", [])
        if not sr_levels:
            return None

        try:
            bbfs = detect_breakout_bar_failure(h1_df, sr_levels)
        except Exception:
            return None

        for bbf in bbfs:
            # BBF bias is the counter-direction (e.g., bearish BBF = bias -1)
            if bbf.get("bias") == -direction and bbf.get("bar_offset", 99) <= 2:
                log.warning(
                    f"{ctx['symbol']} #{ctx['ticket']}: "
                    f"BBF AGAINST position — {bbf.get('name', '')}"
                )
                return "tighten"

        return None

    def _check_trend_integrity(
        self, h1_df, direction: int,
    ) -> str | None:
        """
        Check if the H1 pivot trend has broken against our position (Ch. 10).

        If we're long and H1 pivots now show a downtrend, the thesis
        on the trading timeframe is weakening or broken.

        Returns: "broken" | "weakening" | None
        """
        try:
            pivots = find_pivots(h1_df)
            if not pivots:
                return None
            pivots = classify_pivots_major_minor(pivots)
            trend = determine_trend_from_pivots(pivots)
        except Exception:
            return None

        pv_trend = trend.get("trend", "range")

        if direction == 1 and pv_trend == "downtrend":
            return "broken"
        if direction == -1 and pv_trend == "uptrend":
            return "broken"
        if pv_trend == "range":
            return "weakening"

        return None

    # ═════════════════════════════════════════════════════════════════════════
    #  STOP MANAGEMENT HELPERS
    # ═════════════════════════════════════════════════════════════════════════

    def _compute_structure_sl(
        self, h1_df, direction: int, current_price: float,
        open_price: float, current_atr: float,
    ) -> float:
        """
        Find the best structural stop level from H1 pivot structure.

        For BUY:  SL below the most recent pivot LOW that's above entry.
        For SELL: SL above the most recent pivot HIGH that's below entry.

        This implements the "line in the sand" concept (Ch. 7):
        use actual price structure as stop references, not arbitrary
        ATR distances.  A pivot that held = the market told us where
        buyers/sellers stepped in.
        """
        try:
            pivots = find_pivots(h1_df)
        except Exception:
            return 0.0
        if not pivots:
            return 0.0

        buffer = current_atr * cfg.STRUCTURE_SL_BUFFER_ATR

        if direction == 1:
            # Find pivot lows between entry and current price
            # These are the "higher lows" that confirm the uptrend
            valid_lows = [
                p for p in pivots
                if p["type"] == "low"
                and p["price"] > open_price
                and p["price"] < current_price
            ]
            if valid_lows:
                # Use the most recent one (strongest reference)
                return valid_lows[-1]["price"] - buffer
        else:
            # Find pivot highs between current price and entry
            valid_highs = [
                p for p in pivots
                if p["type"] == "high"
                and p["price"] < open_price
                and p["price"] > current_price
            ]
            if valid_highs:
                return valid_highs[-1]["price"] + buffer

        return 0.0

    def _emergency_tighten(
        self, pos: dict, direction: int, current_price: float,
        current_atr: float, current_sl: float,
        ticket: int, symbol: str, reason: str,
    ) -> float:
        """
        Aggressively tighten stop to 0.3 ATR from current price.

        FIXED vs old code: no longer requires tight_sl > open_price.
        When bar-by-bar signals scream "exit" and we're underwater,
        we STILL tighten — reducing the maximum loss even if the trade
        is currently losing.  This is the correct behavior per Ch. 7:
        "when circumstances change, we need to change our bias."

        Returns the new SL value (or unchanged if tighten failed).
        """
        tight_distance = current_atr * 0.3

        if direction == 1:
            tight_sl = current_price - tight_distance
            if tight_sl > current_sl:
                if self.executor.modify_sl_tp(pos, new_sl=tight_sl):
                    log.info(
                        f"{symbol} #{ticket}: EMERGENCY TIGHTEN — "
                        f"{reason} — SL → {tight_sl:.5f}"
                    )
                    return tight_sl
        else:
            tight_sl = current_price + tight_distance
            if current_sl == 0 or tight_sl < current_sl:
                if self.executor.modify_sl_tp(pos, new_sl=tight_sl):
                    log.info(
                        f"{symbol} #{ticket}: EMERGENCY TIGHTEN — "
                        f"{reason} — SL → {tight_sl:.5f}"
                    )
                    return tight_sl

        return current_sl

    def _apply_breakeven(
        self, pos: dict, direction: int, open_price: float,
        current_sl: float, spread_buffer: float,
        ticket: int, symbol: str,
    ) -> float:
        """Move stop to breakeven (entry + spread buffer)."""
        if direction == 1:
            be_sl = open_price + spread_buffer
            if be_sl > current_sl:
                if self.executor.modify_sl_tp(pos, new_sl=be_sl):
                    self._breakeven_set.add(ticket)
                    log.info(f"{symbol} #{ticket}: moved to breakeven @ {be_sl:.5f}")
                    return be_sl
        else:
            be_sl = open_price - spread_buffer
            if current_sl == 0 or be_sl < current_sl:
                if self.executor.modify_sl_tp(pos, new_sl=be_sl):
                    self._breakeven_set.add(ticket)
                    log.info(f"{symbol} #{ticket}: moved to breakeven @ {be_sl:.5f}")
                    return be_sl

        return current_sl

    # ═════════════════════════════════════════════════════════════════════════
    #  CLOSED POSITION HANDLING
    # ═════════════════════════════════════════════════════════════════════════

    def handle_closed_positions(self, previously_open: set[int]):
        """
        Detect positions that have been closed (by SL/TP) since last check.
        Record the result in the risk manager and send alerts.
        """
        current_tickets = {
            pos.get("ticket", 0) for pos in self.mt5.our_positions()
        }
        closed_tickets = previously_open - current_tickets

        for ticket in closed_tickets:
            # Clean up ALL tracking state
            self._partial_closed.discard(ticket)
            self._breakeven_set.discard(ticket)
            self._trade_contexts.pop(ticket, None)

            pnl = 0.0
            symbol = "?"
            try:
                from_date = datetime.now(timezone.utc) - timedelta(days=30)
                to_date = datetime.now(timezone.utc) + timedelta(seconds=60)
                deals = self.mt5.history_deals(from_date, to_date)
                for deal in deals:
                    if deal.get("position_id") == ticket:
                        pnl += (
                            deal.get("profit", 0)
                            + deal.get("commission", 0)
                            + deal.get("swap", 0)
                        )
                        symbol = deal.get("symbol", symbol)
            except Exception as e:
                deals = []
                log.warning(f"Could not get deal history for #{ticket}: {e}")

            # Determine direction and entry price for cooldown tracking
            direction = "BUY"
            entry_price = 0.0
            try:
                for deal in deals:
                    if deal.get("position_id") == ticket:
                        if deal.get("entry", 0) == 0:  # DEAL_ENTRY_IN
                            entry_price = deal.get("price", 0.0)
                            direction = (
                                "BUY"
                                if deal.get("type", 0) == 0
                                else "SELL"
                            )
            except Exception:
                pass

            # Record in risk manager
            self.risk.record_trade_result(pnl, pnl > 0)
            self.risk.record_symbol_close(
                symbol, won=(pnl > 0), direction=direction,
                entry_price=entry_price,
            )

            log.info(f"Position #{ticket} ({symbol}) closed — PnL=${pnl:.2f}")

            try:
                balance = self.mt5.account_balance()
                self.alerter.custom(
                    f"<b>POSITION CLOSED</b>\n"
                    f"{symbol} #{ticket}\n"
                    f"P/L: ${pnl:+,.2f}\n"
                    f"Balance: ${balance:,.2f}"
                )
            except Exception:
                pass

    # ═════════════════════════════════════════════════════════════════════════
    #  OTHER PUBLIC METHODS
    # ═════════════════════════════════════════════════════════════════════════

    def get_open_tickets(self) -> set[int]:
        """Return set of our open position tickets."""
        return {pos.get("ticket", 0) for pos in self.mt5.our_positions()}

    def check_weekend_protection(self):
        """
        Close all positions before the weekend to avoid gap risk.

        Triggers on Friday after 16:30 ET (New York time).  This is
        30 minutes before the official forex close at 17:00 ET — enough
        margin for execution while spreads are still reasonable.

        Uses NY timezone (via market_hours) so it automatically handles
        US DST transitions.

        NOTE: The system already stops opening new trades at Friday
        12:00 ET (via market_hours.is_new_trade_allowed).  This close
        is the BACKSTOP for positions that survived the afternoon.
        """
        now = market_hours.utcnow()
        from zoneinfo import ZoneInfo
        ny = now.astimezone(ZoneInfo("America/New_York"))

        if ny.weekday() == 4 and (ny.hour > 16 or (ny.hour == 16 and ny.minute >= 30)):
            positions = self.mt5.our_positions()
            if positions:
                log.warning(
                    f"WEEKEND PROTECTION: Closing {len(positions)} positions "
                    f"before market close (NY time: {ny.strftime('%H:%M')})"
                )
                self.emergency_close_all(reason="weekend_protection")

    def emergency_close_all(self, reason: str = "emergency"):
        """Close ALL our open positions immediately."""
        positions = self.mt5.our_positions()
        closed = 0
        failed = 0
        for pos in positions:
            result = self.executor.close_position(pos, reason=reason)
            if result:
                closed += 1
            else:
                failed += 1
                for attempt in range(2):
                    _time.sleep(1 * (attempt + 1))
                    result = self.executor.close_position(pos, reason=reason)
                    if result:
                        closed += 1
                        failed -= 1
                        break

        if positions:
            status = f"Closed {closed}/{len(positions)} positions."
            if failed:
                status += f" FAILED to close {failed}!"
            self.alerter.safety_event(
                "EMERGENCY CLOSE ALL",
                f"{status} Reason: {reason}",
                self.mt5.account_balance(),
            )
            log.warning(f"EMERGENCY: {status} — {reason}")
