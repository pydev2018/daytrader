"""
===============================================================================
  Multi-Timeframe Confluence Engine  (Pristine Method Integration)
===============================================================================
  The alpha generator.  We only trade when multiple timeframes AGREE.

  Architecture (post-Pristine integration):
    Higher TF → Stage classification (Ch. 1) + Pivot trend (Ch. 10)
    Trading TF → Retracement analysis (Ch. 6) + S/R proximity (Ch. 3)
    Entry TF → Candle signal (Ch. 2) + Sweet spot (Ch. 12)

  Design principle: Price is the ONLY truth.  Indicators are visual aids.
  Stage analysis and pivot trends are the PRIMARY decision framework.
  Indicators are demoted to a 5% confirmation role.
===============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from core.indicators import compute_all_indicators, determine_trend, determine_trend_pristine, trend_strength
from core.patterns import scan_candlestick_patterns, scan_chart_patterns, scan_patterns_with_context
from core.structures import (
    find_sr_levels,
    find_supply_demand_zones,
    nearest_sr,
    price_near_level,
    classify_structure,
    aggregate_multi_tf_sr,
)
from core.smart_money import analyze_smart_money
from core.pristine import (
    classify_candle,
    classify_last_n_candles,
    find_pivots,
    classify_pivots_major_minor,
    determine_trend_from_pivots,
    classify_stage,
    analyze_retracement,
    classify_volume,
    detect_sweet_sour_spot,
    detect_pristine_setup,
    detect_breakout_bar_failure,
    find_price_voids,
)
from core.mt5_connector import MT5Connector
from utils.logger import get_logger
from utils import market_hours

log = get_logger("confluence")

# Timeframe hierarchy: higher → lower
TF_HIERARCHY = ["W1", "D1", "H4", "H1", "M15", "M5"]

# Which TFs serve which role
HIGHER_TFS = ["W1", "D1", "H4"]
TRADING_TFS = ["H1", "M15"]
ENTRY_TFS = ["M5", "M15"]


@dataclass
class TimeframeAnalysis:
    """Analysis results for a single timeframe."""
    symbol: str = ""
    timeframe: str = ""
    trend: str = "NEUTRAL"
    trend_strength: float = 0.0
    structure: str = "range"
    sr_levels: list = field(default_factory=list)
    sd_zones: list = field(default_factory=list)
    candle_patterns: list = field(default_factory=list)
    chart_patterns: list = field(default_factory=list)
    smart_money: dict = field(default_factory=dict)
    indicators: dict = field(default_factory=dict)
    df: Optional[pd.DataFrame] = None
    # ── Pristine Method fields (Ch. 1, 2, 5, 6, 10) ─────────────────────
    pivots: list = field(default_factory=list)
    stage: dict = field(default_factory=dict)
    pivot_trend: dict = field(default_factory=dict)
    retracement: dict = field(default_factory=dict)
    volume_class: dict = field(default_factory=dict)
    candle_class: list = field(default_factory=list)
    price_voids: list = field(default_factory=list)
    pristine_trend: dict = field(default_factory=dict)


@dataclass
class SymbolAnalysis:
    """Complete multi-timeframe analysis for a single symbol."""
    symbol: str = ""
    timeframes: dict[str, TimeframeAnalysis] = field(default_factory=dict)
    higher_tf_bias: str = "NEUTRAL"
    trading_tf_bias: str = "NEUTRAL"
    entry_tf_bias: str = "NEUTRAL"
    overall_bias: str = "NEUTRAL"
    confluence_score: float = 0.0
    trade_direction: Optional[str] = None  # "BUY" or "SELL" or None
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    atr: float = 0.0
    spread_pips: float = 0.0
    spread_price: float = 0.0  # FIXED: actual bid-ask spread in price units
    # ── Pristine Method aggregate fields ─────────────────────────────────
    sweet_spot: dict = field(default_factory=dict)
    pristine_setup: dict = field(default_factory=dict)
    multi_tf_sr: list = field(default_factory=list)
    higher_tf_stage: int = 0
    bbf_signals: list = field(default_factory=list)


def analyze_timeframe(
    mt5: MT5Connector,
    symbol: str,
    timeframe: str,
) -> TimeframeAnalysis:
    """Run full analysis on a single symbol/timeframe pair."""
    tfa = TimeframeAnalysis(symbol=symbol, timeframe=timeframe)

    df = mt5.get_rates(symbol, timeframe)
    # FIXED: require enough bars for the slowest indicator (200-period EMA)
    min_bars = max(cfg.EMA_TREND + 50, cfg.ICHI_SENKOU_B + cfg.ICHI_KIJUN + 10, 100)
    if df is None or len(df) < min_bars:
        return tfa

    # Compute all indicators
    df = compute_all_indicators(df)
    tfa.df = df

    # ── PRISTINE ANALYSIS (primary — Ch. 1, 2, 5, 6, 10) ────────────────

    # Pivots & pivot-based trend (Ch. 10)
    pivots = find_pivots(df)
    pivots = classify_pivots_major_minor(pivots)
    tfa.pivots = pivots

    pivot_trend = determine_trend_from_pivots(pivots)
    tfa.pivot_trend = pivot_trend

    # Stage classification (Ch. 1)
    stage = classify_stage(df, pivot_trend=pivot_trend)
    tfa.stage = stage

    # Full Pristine trend determination
    pristine_result = determine_trend_pristine(df)
    tfa.pristine_trend = pristine_result

    # Use Pristine trend as the PRIMARY trend
    tfa.trend = pristine_result.get("trend", "NEUTRAL")
    tfa.trend_strength = trend_strength(df)

    # Retracement analysis (Ch. 6)
    direction = 1 if pivot_trend["trend"] == "uptrend" else (-1 if pivot_trend["trend"] == "downtrend" else 0)
    if direction != 0:
        tfa.retracement = analyze_retracement(df, pivots, direction)

    # Volume classification (Ch. 5)
    if direction != 0:
        tfa.volume_class = classify_volume(df, pivots, direction)

    # Candle classification (Ch. 2)
    tfa.candle_class = classify_last_n_candles(df, n=5)

    # Price voids (Ch. 3)
    tfa.price_voids = find_price_voids(df)

    # ── LEGACY ANALYSIS (secondary — kept for backward compat) ───────────

    # Structure
    tfa.structure = classify_structure(df)

    # S/R levels (enhanced with pivot data)
    tfa.sr_levels = find_sr_levels(df)

    # Supply/demand zones
    tfa.sd_zones = find_supply_demand_zones(df)

    # Candlestick patterns — now context-aware (Ch. 2)
    last_candle = classify_candle(df, idx=-1) if len(df) >= 12 else None
    tfa.candle_patterns = scan_patterns_with_context(
        df,
        sr_levels=tfa.sr_levels,
        stage=stage,
        pristine_candle=last_candle,
    )

    # Chart patterns
    tfa.chart_patterns = scan_chart_patterns(df)

    # Smart money concepts
    tfa.smart_money = analyze_smart_money(df)

    # Snapshot of key indicator values (last bar) — still useful for logging
    last = df.iloc[-1]
    tfa.indicators = {
        "close": last["close"],
        "rsi": last.get("rsi", np.nan),
        "macd": last.get("macd", np.nan),
        "macd_hist": last.get("macd_hist", np.nan),
        "adx": last.get("adx", np.nan),
        "atr": last.get("atr", np.nan),
        "bb_pct_b": last.get("bb_pct_b", np.nan),
        "stoch_k": last.get("stoch_k", np.nan),
        "cci": last.get("cci", np.nan),
        "ema_fast": last.get("ema_fast", np.nan),
        "ema_trend": last.get("ema_trend", np.nan),
        "vol_ratio": last.get("vol_ratio", np.nan),
        "squeeze": last.get("squeeze", False),
        "rsi_divergence": last.get("rsi_divergence", 0),
        "macd_divergence": last.get("macd_divergence", 0),
    }

    return tfa


def analyze_symbol(mt5_conn: MT5Connector, symbol: str) -> SymbolAnalysis:
    """
    Run multi-timeframe analysis on a symbol.
    This is the core of the confluence engine — now Pristine-integrated.
    """
    sa = SymbolAnalysis(symbol=symbol)

    # Analyze each timeframe
    for tf in TF_HIERARCHY:
        tfa = analyze_timeframe(mt5_conn, symbol, tf)
        sa.timeframes[tf] = tfa

    # ── PRISTINE: Stage-based bias (Ch. 1) ─────────────────────────────────
    # The higher TF stage is the PRIMARY direction gate.
    # We check D1 first, then H4 as fallback.
    htf_stage = {}
    for tf in ["D1", "H4", "W1"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.stage.get("confidence", 0) > 0.3:
            htf_stage = tfa.stage
            sa.higher_tf_stage = htf_stage.get("stage", 0)
            break

    # ── Higher-TF bias from stages ─────────────────────────────────────────
    higher_biases = []
    for tf in HIGHER_TFS:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.trend != "NEUTRAL":
            weight = 3 if tf == "D1" else (2 if tf == "H4" else 1)
            val = 1 if tfa.trend == "BULLISH" else -1
            higher_biases.extend([val] * weight)

    if higher_biases:
        avg = sum(higher_biases) / len(higher_biases)
        sa.higher_tf_bias = "BULLISH" if avg > 0.2 else ("BEARISH" if avg < -0.2 else "NEUTRAL")

    # ── Trading-TF bias ────────────────────────────────────────────────────
    trading_biases = []
    for tf in TRADING_TFS:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.trend != "NEUTRAL":
            val = 1 if tfa.trend == "BULLISH" else -1
            trading_biases.append(val)

    if trading_biases:
        avg = sum(trading_biases) / len(trading_biases)
        sa.trading_tf_bias = "BULLISH" if avg > 0 else ("BEARISH" if avg < 0 else "NEUTRAL")

    # ── Entry-TF bias ──────────────────────────────────────────────────────
    entry_biases = []
    for tf in ENTRY_TFS:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.trend != "NEUTRAL":
            val = 1 if tfa.trend == "BULLISH" else -1
            entry_biases.append(val)

    if entry_biases:
        avg = sum(entry_biases) / len(entry_biases)
        sa.entry_tf_bias = "BULLISH" if avg > 0 else ("BEARISH" if avg < 0 else "NEUTRAL")

    # ── Overall bias: Stage-first, then alignment ──────────────────────────
    # Pristine principle: only trade in Stage 2 (buy) or Stage 4 (sell)
    if htf_stage.get("tradeable"):
        allowed = htf_stage.get("allowed_direction")
        if allowed == "BUY" and sa.trading_tf_bias in ("BULLISH", "NEUTRAL"):
            sa.overall_bias = "BULLISH"
        elif allowed == "SELL" and sa.trading_tf_bias in ("BEARISH", "NEUTRAL"):
            sa.overall_bias = "BEARISH"
        else:
            sa.overall_bias = "NEUTRAL"
    elif sa.higher_tf_bias == sa.trading_tf_bias and sa.higher_tf_bias != "NEUTRAL":
        # Fallback: classic alignment (for weak stage confidence)
        sa.overall_bias = sa.higher_tf_bias
    else:
        sa.overall_bias = "NEUTRAL"

    # ── Trade direction ──────────────────────────────────────────────────
    if sa.overall_bias == "BULLISH" and sa.entry_tf_bias in ("BULLISH", "NEUTRAL"):
        sa.trade_direction = "BUY"
    elif sa.overall_bias == "BEARISH" and sa.entry_tf_bias in ("BEARISH", "NEUTRAL"):
        sa.trade_direction = "SELL"
    else:
        sa.trade_direction = None

    # ── ATR & spread ─────────────────────────────────────────────────────
    h1 = sa.timeframes.get("H1")
    if h1 and h1.indicators:
        atr_val = h1.indicators.get("atr", np.nan)
        sa.atr = atr_val if not np.isnan(atr_val) else 0.0

    sa.spread_pips = mt5_conn.spread_pips(symbol)

    # FIXED: Store actual bid-ask spread in price units for accurate
    # spread-as-percent-of-risk penalty (works for ANY instrument).
    tick = mt5_conn.symbol_tick(symbol)
    if tick is not None:
        sa.spread_price = tick["ask"] - tick["bid"]

    # ── Multi-TF S/R aggregation (Ch. 3) ─────────────────────────────────
    tf_sr = {}
    for tf in TF_HIERARCHY:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.sr_levels:
            tf_sr[tf] = tfa.sr_levels
    if tf_sr:
        entry_tf = sa.timeframes.get("M15") or sa.timeframes.get("H1")
        cp = entry_tf.indicators.get("close", 0) if entry_tf and entry_tf.indicators else 0
        sa.multi_tf_sr = aggregate_multi_tf_sr(tf_sr, cp)

    # ── Sweet Spot / Sour Spot detection (Ch. 12) ────────────────────────
    if sa.trade_direction:
        direction_val = 1 if sa.trade_direction == "BUY" else -1
        tf_data = {}
        for tf in TF_HIERARCHY:
            tfa = sa.timeframes.get(tf)
            if tfa:
                tf_data[tf] = {
                    "stage": tfa.stage,
                    "pivot_trend": tfa.pivot_trend,
                    "retracement": tfa.retracement,
                    "sr_levels": tfa.sr_levels,
                    "candle_class": tfa.candle_class,
                    "current_price": tfa.indicators.get("close", 0) if tfa.indicators else 0,
                    "atr": tfa.indicators.get("atr", 0) if tfa.indicators else 0,
                }
        sa.sweet_spot = detect_sweet_sour_spot(
            tf_data, direction_val,
            entry_tf="M15", trend_tf="H1", higher_tf="D1",
        )

    # ── Compute SL / TP if we have a direction ───────────────────────────
    if sa.trade_direction and sa.atr > 0:
        entry_tf = sa.timeframes.get("M15") or sa.timeframes.get("H1")
        if entry_tf and entry_tf.df is not None:
            current_price = entry_tf.indicators.get("close", 0)
            sa.entry_price = current_price

            # Get broker's minimum stop level for this symbol
            sym_info = mt5_conn.symbol_info(symbol)
            min_stop_level = 0
            if sym_info:
                stops_level = sym_info.get("trade_stops_level", 0)
                point = sym_info.get("point", 0.00001)
                min_stop_level = stops_level * point

            # ── Pristine SL/TP: pivot-based (Ch. 13) ────────────────────
            # Try to use pivots for SL/TP first (Pristine method)
            h1_tfa = sa.timeframes.get("H1")
            pivot_sl, pivot_tp = _pristine_sl_tp(sa, h1_tfa)

            if pivot_sl > 0 and pivot_tp > 0:
                sa.stop_loss = pivot_sl
                sa.take_profit = pivot_tp
            else:
                # Fallback: ATR-based SL/TP
                if sa.trade_direction == "BUY":
                    sa.stop_loss = current_price - cfg.ATR_SL_MULTIPLIER * sa.atr
                    sa.take_profit = current_price + cfg.ATR_TP_MULTIPLIER * sa.atr
                else:
                    sa.stop_loss = current_price + cfg.ATR_SL_MULTIPLIER * sa.atr
                    sa.take_profit = current_price - cfg.ATR_TP_MULTIPLIER * sa.atr

            # Enforce broker's minimum stop level
            if min_stop_level > 0:
                if sa.trade_direction == "BUY":
                    min_sl = current_price - min_stop_level
                    min_tp = current_price + min_stop_level
                    if sa.stop_loss > min_sl:
                        sa.stop_loss = min_sl
                    if sa.take_profit < min_tp:
                        sa.take_profit = min_tp
                else:
                    max_sl = current_price + min_stop_level
                    max_tp = current_price - min_stop_level
                    if sa.stop_loss < max_sl:
                        sa.stop_loss = max_sl
                    if sa.take_profit > max_tp:
                        sa.take_profit = max_tp

            # Adjust SL to be beyond nearest S/R level
            _adjust_sl_to_structure(sa, entry_tf)

    # ── BBF detection (Ch. 13) ───────────────────────────────────────────
    for tf in ["H1", "M15"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.df is not None and tfa.sr_levels:
            bbfs = detect_breakout_bar_failure(tfa.df, tfa.sr_levels)
            for b in bbfs:
                b["timeframe"] = tf
            sa.bbf_signals.extend(bbfs)

    # ── Compute confluence score ─────────────────────────────────────────
    compute_confluence_score(sa)

    # ── Free large DataFrames to reduce memory ───────────────────────────
    for tf in TF_HIERARCHY:
        tfa = sa.timeframes.get(tf)
        if tfa:
            tfa.df = None

    return sa


def _pristine_sl_tp(sa: SymbolAnalysis, h1_tfa) -> tuple[float, float]:
    """
    Derive SL and TP from pivot structure (Ch. 13).

    SL: Just beyond the pivot that created the setup.
        BUY → below the most recent pivot low + buffer
        SELL → above the most recent pivot high + buffer

    TP: Next major S/R level in the trade direction, or prior swing extreme.
    """
    sl = 0.0
    tp = 0.0
    buffer = sa.atr * 0.2  # small buffer beyond the pivot

    if not h1_tfa or not h1_tfa.pivots:
        return sl, tp

    pivots = h1_tfa.pivots
    p_highs = [p for p in pivots if p["type"] == "high"]
    p_lows = [p for p in pivots if p["type"] == "low"]

    if sa.trade_direction == "BUY":
        # SL below the most recent pivot low
        if p_lows:
            sl = p_lows[-1]["price"] - buffer
        # TP at the NEAREST pivot high above entry (not the most extreme).
        # Targeting the nearest realistic resistance gives a realistic R:R
        # and higher TP-hit rate than targeting the most distant pivot.
        if p_highs:
            above = sorted(
                [p["price"] for p in p_highs if p["price"] > sa.entry_price]
            )
            if above:
                tp = above[0]  # nearest pivot high above entry
            else:
                tp = sa.entry_price + cfg.ATR_TP_MULTIPLIER * sa.atr

        # Also check multi-TF S/R: use nearest major resistance as TP
        # cap (don't push TP *past* a major resistance the price must break)
        if sa.multi_tf_sr:
            major_above = sorted(
                [lvl["price"] for lvl in sa.multi_tf_sr
                 if lvl.get("kind") in ("R", "SR")
                 and lvl["price"] > sa.entry_price
                 and lvl.get("major")]
            )
            if major_above:
                tp = min(tp, major_above[0])  # cap at nearest major R

    elif sa.trade_direction == "SELL":
        # SL above the most recent pivot high
        if p_highs:
            sl = p_highs[-1]["price"] + buffer
        # TP at the NEAREST pivot low below entry
        if p_lows:
            below = sorted(
                [p["price"] for p in p_lows if p["price"] < sa.entry_price],
                reverse=True,
            )
            if below:
                tp = below[0]  # nearest pivot low below entry
            else:
                tp = sa.entry_price - cfg.ATR_TP_MULTIPLIER * sa.atr

        # Cap TP at nearest major support
        if sa.multi_tf_sr:
            major_below = sorted(
                [lvl["price"] for lvl in sa.multi_tf_sr
                 if lvl.get("kind") in ("S", "SR")
                 and lvl["price"] < sa.entry_price
                 and lvl.get("major")],
                reverse=True,
            )
            if major_below:
                tp = max(tp, major_below[0])  # cap at nearest major S

    return sl, tp


def _adjust_sl_to_structure(sa: SymbolAnalysis, entry_tfa: TimeframeAnalysis):
    """
    Ensure the SL is beyond the nearest S/R level.
    If SL sits ON a support/resistance level, it'll get hunted.
    Move it a little further.
    """
    if not entry_tfa or not entry_tfa.sr_levels or sa.atr == 0:
        return

    buffer = sa.atr * 0.15

    for level in entry_tfa.sr_levels:
        lvl_price = level.get("price", 0)
        if lvl_price == 0:
            continue

        if sa.trade_direction == "BUY":
            # If SL is above or right at a support level, push it below
            if sa.stop_loss > lvl_price and abs(sa.stop_loss - lvl_price) < sa.atr * 0.5:
                sa.stop_loss = lvl_price - buffer
                break
        elif sa.trade_direction == "SELL":
            # If SL is below or right at a resistance level, push it above
            if sa.stop_loss < lvl_price and abs(sa.stop_loss - lvl_price) < sa.atr * 0.5:
                sa.stop_loss = lvl_price + buffer
                break


# ═════════════════════════════════════════════════════════════════════════════
#  CONFLUENCE SCORING — Pristine-Weighted  (Ch. 1-13)
# ═════════════════════════════════════════════════════════════════════════════

def compute_confluence_score(sa: SymbolAnalysis) -> float:
    """
    Compute the confidence/confluence score (0-100) for a potential trade.
    This is the gatekeeper — only trades scoring >= CONFIDENCE_THRESHOLD pass.

    PRISTINE METHOD ARCHITECTURE:
      Hard gates first (instant reject if failed), then weighted scoring.

      Weights (must sum to 100):
        stage_alignment       = 20  (Ch. 1)
        pivot_trend_quality   = 15  (Ch. 10)
        sweet_spot_score      = 15  (Ch. 12)
        sr_level_quality      = 15  (Ch. 3)
        retracement_quality   = 10  (Ch. 6)
        candle_signal_quality = 10  (Ch. 2)
        volume_classification =  5  (Ch. 5)
        indicator_confirmation=  5  (Ch. 4, 16)
        spread_quality        =  5  (practical)

      Penalties retained from previous implementation:
        trend_exhaustion    (0 to -15)
        higher_tf_reversal  (0 to -20)
    """
    if sa.trade_direction is None:
        return 0.0

    weights = cfg.CONFIDENCE_WEIGHTS
    score = 0.0
    direction_val = 1 if sa.trade_direction == "BUY" else -1

    # ══════════════════════════════════════════════════════════════════════
    #  HARD GATES — instant rejection (Ch. 1, 10, 12)
    # ══════════════════════════════════════════════════════════════════════
    gate_passed, gate_reason = _pristine_hard_gates(sa, direction_val)
    if not gate_passed:
        log.debug(f"{sa.symbol}: HARD GATE REJECT — {gate_reason}")
        sa.confluence_score = 0.0
        return 0.0

    # ══════════════════════════════════════════════════════════════════════
    #  1. Stage Alignment (0-20)  —  Ch. 1
    # ══════════════════════════════════════════════════════════════════════
    stage_score = 0.0
    htf_stage = {}

    # Use the highest-confidence stage from D1/H4
    for tf in ["D1", "H4"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.stage:
            if tfa.stage.get("confidence", 0) > htf_stage.get("confidence", 0):
                htf_stage = tfa.stage

    if htf_stage:
        stage_num = htf_stage.get("stage", 0)
        stage_conf = htf_stage.get("confidence", 0)

        if (direction_val == 1 and stage_num == 2) or \
           (direction_val == -1 and stage_num == 4):
            stage_score = stage_conf  # 0.0 - 1.0
        elif stage_num in (1, 3):
            stage_score = 0.0  # No-trade stages
        else:
            stage_score = 0.2  # Wrong stage but not dead zone

    score += stage_score * weights.get("stage_alignment", 20)

    # ══════════════════════════════════════════════════════════════════════
    #  2. Pivot Trend Quality (0-15)  —  Ch. 10
    # ══════════════════════════════════════════════════════════════════════
    pivot_score = 0.0
    # Use H1 pivot trend as primary (trading timeframe)
    h1 = sa.timeframes.get("H1")
    if h1 and h1.pivot_trend:
        pv = h1.pivot_trend
        pv_trend = pv.get("trend", "range")
        pv_strength = pv.get("strength", "weak")

        if (direction_val == 1 and pv_trend == "uptrend") or \
           (direction_val == -1 and pv_trend == "downtrend"):
            if pv_strength == "strong":
                pivot_score = 1.0
            elif pv_strength == "moderate":
                pivot_score = 0.7
            else:
                pivot_score = 0.4
        elif pv_trend == "range":
            pivot_score = 0.15
        else:
            pivot_score = 0.0  # Counter-trend

    score += pivot_score * weights.get("pivot_trend_quality", 15)

    # ══════════════════════════════════════════════════════════════════════
    #  3. Sweet Spot Score (0-15)  —  Ch. 12
    # ══════════════════════════════════════════════════════════════════════
    sweet_score = 0.0
    if sa.sweet_spot:
        spot_type = sa.sweet_spot.get("type", "neutral")
        spot_val = sa.sweet_spot.get("score", 0)

        if spot_type == "sweet_spot":
            sweet_score = max(0.5, min(1.0, 0.5 + spot_val))
        elif spot_type == "sour_spot":
            sweet_score = max(0.0, 0.2 + spot_val)
        else:
            sweet_score = 0.3  # neutral

    score += sweet_score * weights.get("sweet_spot_score", 15)

    # ══════════════════════════════════════════════════════════════════════
    #  4. S/R Level Quality (0-15)  —  Ch. 3
    # ══════════════════════════════════════════════════════════════════════
    sr_score = 0.0

    # Prefer multi-TF aggregated S/R, fallback to single-TF
    sr_to_check = sa.multi_tf_sr or []
    if not sr_to_check:
        entry_tfa = sa.timeframes.get("H1") or sa.timeframes.get("M15")
        if entry_tfa:
            sr_to_check = entry_tfa.sr_levels

    if sr_to_check and sa.atr > 0:
        near_levels = price_near_level(sa.entry_price, sr_to_check, sa.atr, proximity_atr=2.0)
        if near_levels:
            best = near_levels[0]
            strength = min(best.get("strength", 0), 1.0)
            is_major = best.get("major", False)

            # Buying near support or selling near resistance = ideal
            kind = best.get("kind", "")
            if (direction_val == 1 and kind in ("S", "SR")) or \
               (direction_val == -1 and kind in ("R", "SR")):
                sr_score = strength
                if is_major:
                    sr_score = min(sr_score + 0.15, 1.0)
            elif (direction_val == 1 and kind == "R") or \
                 (direction_val == -1 and kind == "S"):
                # Buying into resistance or selling into support = penalty
                sr_score = max(0, strength * 0.3)

    score += sr_score * weights.get("sr_level_quality", 15)

    # ══════════════════════════════════════════════════════════════════════
    #  5. Retracement Quality (0-10)  —  Ch. 6
    # ══════════════════════════════════════════════════════════════════════
    ret_score = 0.0
    # Use H1 retracement (trading TF)
    if h1 and h1.retracement:
        ret = h1.retracement
        quality = ret.get("quality", "unknown")
        near_20 = ret.get("near_ma20", False)

        if quality == "pristine":
            ret_score = 1.0
        elif quality == "healthy":
            ret_score = 0.8
        elif quality == "deep":
            ret_score = 0.4
        elif quality == "none":
            ret_score = 0.5  # extending, acceptable
        elif quality == "failing":
            ret_score = 0.1
        else:
            ret_score = 0.3

        # Bonus for pullback to 20 EMA area (Ch. 6 textbook setup)
        if near_20 and quality in ("pristine", "healthy"):
            ret_score = min(ret_score + 0.15, 1.0)

    score += ret_score * weights.get("retracement_quality", 10)

    # ══════════════════════════════════════════════════════════════════════
    #  6. Candle Signal Quality (0-10)  —  Ch. 2
    # ══════════════════════════════════════════════════════════════════════
    candle_score = 0.0

    # Best candle pattern from entry TFs, now context-weighted
    best_candle = 0.0
    for tf in ["M15", "H1", "M5"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.candle_patterns:
            for pat in tfa.candle_patterns:
                if pat["bias"] == direction_val:
                    best_candle = max(best_candle, pat["strength"])
                    break
    candle_score = best_candle * 0.7

    # Pristine candle classification bonus (WRB/COG/Tail)
    for tf in ["M15", "H1"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.candle_class:
            last_c = tfa.candle_class[-1]
            if last_c.get("bias") == direction_val:
                if last_c.get("type") == "WRB":
                    candle_score = min(candle_score + 0.2, 1.0)
                if last_c.get("cog") and (
                    (direction_val == 1 and last_c["cog"] == "bullish") or
                    (direction_val == -1 and last_c["cog"] == "bearish")
                ):
                    candle_score = min(candle_score + 0.1, 1.0)
                break

    # BBF signals are very high probability (Ch. 13)
    for bbf in sa.bbf_signals:
        if bbf.get("bias") == direction_val:
            candle_score = min(candle_score + 0.3, 1.0)
            break

    score += candle_score * weights.get("candle_signal_quality", 10)

    # ══════════════════════════════════════════════════════════════════════
    #  7. Volume Classification (0-5)  —  Ch. 5
    # ══════════════════════════════════════════════════════════════════════
    vol_score = 0.0
    if h1 and h1.volume_class:
        vc = h1.volume_class
        if vc.get("vol_confirms_trend"):
            vol_score = 1.0
        elif vc.get("pullback_vol_trend") == "declining":
            vol_score = 0.7
        elif vc.get("current_vol_type") == "professional":
            vol_score = 0.8
        elif vc.get("current_vol_type") == "novice":
            vol_score = 0.1  # Climactic volume at end of move = bad
        elif vc.get("pullback_vol_trend") == "rising":
            vol_score = 0.2
        else:
            vol_score = 0.4

    score += vol_score * weights.get("volume_classification", 5)

    # ══════════════════════════════════════════════════════════════════════
    #  8. Indicator Confirmation (0-5)  —  Ch. 4, 16 (demoted)
    # ══════════════════════════════════════════════════════════════════════
    ind_score = 0.0
    if h1 and h1.indicators:
        ind = h1.indicators
        ind_agree = 0
        ind_total = 0

        # RSI
        rsi = ind.get("rsi")
        if rsi is not None and not np.isnan(rsi):
            ind_total += 1
            if direction_val == 1 and 40 < rsi < 70:
                ind_agree += 1
            elif direction_val == -1 and 30 < rsi < 60:
                ind_agree += 1

        # EMA alignment
        ema_f = ind.get("ema_fast")
        ema_t = ind.get("ema_trend")
        if ema_f is not None and ema_t is not None and not np.isnan(ema_f) and not np.isnan(ema_t):
            ind_total += 1
            if (direction_val == 1 and ema_f > ema_t) or (direction_val == -1 and ema_f < ema_t):
                ind_agree += 1

        # ADX (trending market)
        adx = ind.get("adx")
        if adx is not None and not np.isnan(adx) and adx > cfg.ADX_TREND_THRESHOLD:
            ind_total += 1
            ind_agree += 1

        if ind_total > 0:
            ind_score = ind_agree / ind_total

    score += ind_score * weights.get("indicator_confirmation", 5)

    # ══════════════════════════════════════════════════════════════════════
    #  9. Spread Quality (0-5)
    # ══════════════════════════════════════════════════════════════════════
    if sa.spread_pips <= 1.0:
        score += weights.get("spread_quality", 5)
    elif sa.spread_pips <= 2.0:
        score += weights.get("spread_quality", 5) * 0.8
    elif sa.spread_pips <= cfg.MAX_SPREAD_PIPS:
        score += weights.get("spread_quality", 5) * 0.4

    # ══════════════════════════════════════════════════════════════════════
    #  PENALTIES (retained from prior implementation)
    # ══════════════════════════════════════════════════════════════════════

    # ── Spread-as-percent-of-risk penalty ────────────────────────────────
    if sa.atr > 0 and sa.entry_price > 0 and sa.stop_loss > 0:
        risk_distance = abs(sa.entry_price - sa.stop_loss)
        if risk_distance > 0:
            # FIXED: Use actual bid-ask spread in price units instead of
            # hardcoded 0.0001 conversion.  Works for ALL instrument types.
            spread_price = sa.spread_price if sa.spread_price > 0 else 0.0
            spread_pct_of_risk = spread_price / risk_distance
            if spread_pct_of_risk > 0.15:
                penalty = min(10.0, spread_pct_of_risk * 30)
                score -= penalty

    # ── Trend Exhaustion Penalty (0 to -15) ──────────────────────────────
    exhaustion = _detect_trend_exhaustion(sa, direction_val)
    if exhaustion["penalty"] > 0:
        score -= exhaustion["penalty"]
        log.debug(
            f"{sa.symbol}: trend exhaustion penalty = -{exhaustion['penalty']:.1f} "
            f"(reasons: {', '.join(exhaustion['reasons'])})"
        )

    # ── Higher-TF Reversal Override (0 to -20) ───────────────────────────
    htf_penalty = _higher_tf_reversal_check(sa, direction_val)
    if htf_penalty > 0:
        score -= htf_penalty
        log.debug(
            f"{sa.symbol}: higher-TF reversal penalty = -{htf_penalty:.1f}"
        )

    sa.confluence_score = round(max(min(score, 100.0), 0.0), 2)
    return sa.confluence_score


# ═════════════════════════════════════════════════════════════════════════════
#  HARD GATES  (Ch. 1, 6, 12)
# ═════════════════════════════════════════════════════════════════════════════

def _pristine_hard_gates(sa: SymbolAnalysis, direction_val: int) -> tuple[bool, str]:
    """
    Absolute prerequisites.  If ANY gate fails, score = 0, trade rejected.

    Gate 1 — Retracement Gate (Ch. 6):
        If the H1 retracement > 80%, the trend is broken.  No entry.

    Gate 2 — Sour Spot Gate (Ch. 12):
        If explicitly in a sour spot with very negative score, reject.

    Note: Stage gate is soft (scored at 20 points) rather than hard,
    because stage confidence can be low for instruments with limited data.
    This allows the system to still find opportunities while strongly
    penalizing wrong-stage trades through the scoring.
    """
    # ── Gate 1: Retracement too deep ─────────────────────────────────────
    h1 = sa.timeframes.get("H1")
    if h1 and h1.retracement:
        quality = h1.retracement.get("quality", "")
        if quality == "broken":
            return False, f"H1 retracement = broken ({h1.retracement.get('retracement_pct', 0):.0%}) — trend is dead"

    # ── Gate 2: Deep sour spot ───────────────────────────────────────────
    if sa.sweet_spot:
        if sa.sweet_spot.get("type") == "sour_spot" and sa.sweet_spot.get("score", 0) <= -0.6:
            reasons = sa.sweet_spot.get("reasons", [])
            return False, f"Deep sour spot (score={sa.sweet_spot['score']:.2f}): {'; '.join(reasons[:2])}"

    return True, "OK"


# ═════════════════════════════════════════════════════════════════════════════
#  PENALTY FUNCTIONS (retained from prior implementation)
# ═════════════════════════════════════════════════════════════════════════════

def _detect_trend_exhaustion(
    sa: SymbolAnalysis, direction_val: int
) -> dict:
    """
    Detect whether the current trend is running out of steam.

    A quant measures exhaustion through:
    1. RSI approaching extremes (not yet there, but close)
    2. ADX declining (trend losing momentum even if still "trending")
    3. Shrinking candle bodies (participants losing conviction)
    4. Bollinger Band overextension (price stretched too far)
    5. Price far from EMA (reversion risk)
    6. Volume declining (smart money stepping out)

    Returns {"penalty": float, "reasons": [str]}
    """
    penalty = 0.0
    reasons = []

    for tf_name in ["H4", "H1"]:
        tfa = sa.timeframes.get(tf_name)
        if not tfa or not tfa.indicators:
            continue

        ind = tfa.indicators
        df = tfa.df

        # ── RSI approaching extremes ─────────────────────────────────
        rsi = ind.get("rsi")
        if rsi is not None and not np.isnan(rsi):
            if direction_val == 1 and rsi > 65:
                pen = min(4.0, (rsi - 65) * 0.2)
                penalty += pen
                reasons.append(f"{tf_name} RSI={rsi:.0f} (overbought zone)")
            elif direction_val == -1 and rsi < 35:
                pen = min(4.0, (35 - rsi) * 0.2)
                penalty += pen
                reasons.append(f"{tf_name} RSI={rsi:.0f} (oversold zone)")

        # ── ADX declining (momentum fading) ──────────────────────────
        if df is not None and "adx" in df.columns and len(df) > 5:
            adx_now = df["adx"].iloc[-1]
            adx_3ago = df["adx"].iloc[-4]
            if (not np.isnan(adx_now) and not np.isnan(adx_3ago) and adx_3ago > 0):
                if adx_now < adx_3ago:
                    decline_pct = (adx_3ago - adx_now) / adx_3ago * 100
                    if decline_pct > 10:
                        pen = min(3.0, decline_pct * 0.1)
                        penalty += pen
                        reasons.append(f"{tf_name} ADX declining: {adx_3ago:.0f}→{adx_now:.0f}")

        # ── Shrinking candle bodies ──────────────────────────────────
        if df is not None and len(df) > 10:
            bodies = (df["close"] - df["open"]).abs()
            recent_body = bodies.iloc[-3:].mean()
            older_body = bodies.iloc[-10:-3].mean()
            if older_body > 0 and recent_body > 0:
                body_ratio = recent_body / older_body
                if body_ratio < 0.5:
                    pen = min(3.0, (1 - body_ratio) * 3)
                    penalty += pen
                    reasons.append(f"{tf_name} candle bodies shrinking ({body_ratio:.0%})")

        # ── Bollinger Band overextension ─────────────────────────────
        bb_pct_b = ind.get("bb_pct_b")
        if bb_pct_b is not None and not np.isnan(bb_pct_b):
            if direction_val == 1 and bb_pct_b > 0.90:
                pen = min(3.0, (bb_pct_b - 0.9) * 20)
                penalty += pen
                reasons.append(f"{tf_name} BB%B={bb_pct_b:.2f} (above upper band)")
            elif direction_val == -1 and bb_pct_b < 0.10:
                pen = min(3.0, (0.1 - bb_pct_b) * 20)
                penalty += pen
                reasons.append(f"{tf_name} BB%B={bb_pct_b:.2f} (below lower band)")

        # ── Price far from EMA ───────────────────────────────────────
        close = ind.get("close", 0)
        ema_t = ind.get("ema_trend", 0)
        atr = ind.get("atr", 0)
        if close > 0 and ema_t > 0 and atr > 0 and not np.isnan(ema_t):
            distance_from_ema = abs(close - ema_t) / atr
            if distance_from_ema > 2.5:
                pen = min(3.0, (distance_from_ema - 2.5) * 2)
                penalty += pen
                reasons.append(f"{tf_name} price {distance_from_ema:.1f}x ATR from EMA")

        # ── Volume declining ─────────────────────────────────────────
        vol_ratio = ind.get("vol_ratio")
        if vol_ratio is not None and not np.isnan(vol_ratio) and vol_ratio < 0.7:
            pen = min(2.0, (0.7 - vol_ratio) * 4)
            penalty += pen
            reasons.append(f"{tf_name} vol_ratio={vol_ratio:.2f} (<0.7)")

    penalty = min(penalty, 15.0)
    return {"penalty": round(penalty, 1), "reasons": reasons}


def _higher_tf_reversal_check(
    sa: SymbolAnalysis, direction_val: int
) -> float:
    """
    Check if a HIGHER timeframe shows reversal signals against direction.
    Returns a penalty (0-20).
    """
    penalty = 0.0

    for tf_name, tf_weight in [("D1", 3.0), ("H4", 2.0), ("W1", 1.5)]:
        tfa = sa.timeframes.get(tf_name)
        if not tfa:
            continue

        # Reversal candle patterns on higher TF
        if tfa.candle_patterns:
            for pat in tfa.candle_patterns:
                if pat["bias"] == -direction_val and pat["strength"] >= 0.6:
                    pen = pat["strength"] * tf_weight * 2
                    penalty += pen

        # Divergence on higher TF
        if tfa.indicators:
            rsi_div = tfa.indicators.get("rsi_divergence", 0)
            macd_div = tfa.indicators.get("macd_divergence", 0)
            if rsi_div == -direction_val:
                penalty += tf_weight * 2.5
            if macd_div == -direction_val:
                penalty += tf_weight * 2.0

        # Structure breaks against direction
        if tfa.smart_money:
            sm = tfa.smart_money
            if sm.get("structure_breaks"):
                for brk in sm["structure_breaks"]:
                    if brk.get("bias") == -direction_val:
                        penalty += tf_weight * 3.0

    return min(penalty, 20.0)
