"""
===============================================================================
  Signal Generator — produces actionable trade signals from analysis
===============================================================================
  Pristine Method integration:
    - PBS/PSS (Pristine Buy/Sell Setup) detection as a quality indicator
    - Rationale includes stage, pivot, retracement, sweet spot info
    - Gate checks remain the same (SL/TP validation, R:R, ATR, spread)
===============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import config as cfg
from core.confluence import SymbolAnalysis
from core.pristine import detect_pristine_setup, classify_candle
from utils.logger import get_logger

log = get_logger("signals")


@dataclass
class TradeSignal:
    """A validated, ready-to-execute trade signal."""
    symbol: str
    direction: str              # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float           # 0-100
    win_probability: float      # estimated from confidence mapping
    risk_reward_ratio: float
    atr: float
    spread_pips: float
    rationale: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pristine_setup: str = ""    # "PBS A+", "PSS A", etc. or empty

    @property
    def risk_pips(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    @property
    def reward_pips(self) -> float:
        return abs(self.take_profit - self.entry_price)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "confidence": self.confidence,
            "win_probability": self.win_probability,
            "risk_reward_ratio": self.risk_reward_ratio,
            "atr": self.atr,
            "spread_pips": self.spread_pips,
            "rationale": self.rationale,
            "timestamp": self.timestamp.isoformat(),
            "pristine_setup": self.pristine_setup,
        }


def confidence_to_win_probability(confidence: float) -> float:
    """
    Map confidence score (0-100) to estimated win probability.
    This is a conservative mapping — even high confidence doesn't guarantee wins.

    75  → 0.55
    80  → 0.58
    85  → 0.62
    90  → 0.66
    95+ → 0.70
    """
    if confidence < 75:
        return 0.45
    if confidence < 80:
        return 0.55 + (confidence - 75) * 0.006
    if confidence < 85:
        return 0.58 + (confidence - 80) * 0.008
    if confidence < 90:
        return 0.62 + (confidence - 85) * 0.008
    if confidence < 95:
        return 0.66 + (confidence - 90) * 0.008
    return 0.70


def generate_signal(sa: SymbolAnalysis) -> Optional[TradeSignal]:
    """
    Convert a SymbolAnalysis into a TradeSignal if it passes all filters.
    Returns None if the setup doesn't qualify.
    """
    # ── Gate 1: Must have a direction ────────────────────────────────────
    if sa.trade_direction is None:
        return None

    # ── Gate 2: Use pre-computed confluence score ──────────────────────
    # FIXED: Do NOT recompute here.  analyze_symbol() already computed
    # the score with full DataFrames.  By now tfa.df has been freed
    # (set to None), so recomputing would skip trend-exhaustion penalties
    # that rely on DF access, yielding a higher (weaker) score.
    score = sa.confluence_score
    if score < cfg.CONFIDENCE_THRESHOLD:
        log.debug(f"{sa.symbol}: confidence {score:.1f} < {cfg.CONFIDENCE_THRESHOLD} — skip")
        return None

    # ── Gate 3: Spread filter ────────────────────────────────────────────
    if sa.spread_pips > cfg.MAX_SPREAD_PIPS:
        log.debug(f"{sa.symbol}: spread {sa.spread_pips:.1f} pips too wide — skip")
        return None

    # ── Gate 4: SL/TP validation & Risk/Reward ratio ────────────────────
    if sa.entry_price == 0 or sa.stop_loss == 0 or sa.take_profit == 0:
        return None

    # Validate SL and TP are on the correct side of entry
    if sa.trade_direction == "BUY":
        if sa.stop_loss >= sa.entry_price:
            log.debug(f"{sa.symbol}: BUY SL ({sa.stop_loss}) >= entry ({sa.entry_price}) — invalid")
            return None
        if sa.take_profit <= sa.entry_price:
            log.debug(f"{sa.symbol}: BUY TP ({sa.take_profit}) <= entry ({sa.entry_price}) — invalid")
            return None
    else:  # SELL
        if sa.stop_loss <= sa.entry_price:
            log.debug(f"{sa.symbol}: SELL SL ({sa.stop_loss}) <= entry ({sa.entry_price}) — invalid")
            return None
        if sa.take_profit >= sa.entry_price:
            log.debug(f"{sa.symbol}: SELL TP ({sa.take_profit}) >= entry ({sa.entry_price}) — invalid")
            return None

    risk = sa.entry_price - sa.stop_loss if sa.trade_direction == "BUY" else sa.stop_loss - sa.entry_price
    reward = sa.take_profit - sa.entry_price if sa.trade_direction == "BUY" else sa.entry_price - sa.take_profit
    if risk <= 0 or reward <= 0:
        return None

    rr_ratio = reward / risk
    if rr_ratio < cfg.MIN_RISK_REWARD_RATIO:
        log.debug(f"{sa.symbol}: R:R {rr_ratio:.2f} < {cfg.MIN_RISK_REWARD_RATIO} — skip")
        return None

    # ── Gate 5: ATR sanity ───────────────────────────────────────────────
    if sa.atr <= 0:
        return None

    # ── Detect Pristine Setup (PBS/PSS) ──────────────────────────────────
    pristine_label = ""
    pbs_result = _detect_pbs_pss(sa)
    if pbs_result:
        pristine_label = f"{pbs_result['type']} {pbs_result['quality']}"
        sa.pristine_setup = pbs_result

    # ── Build rationale ──────────────────────────────────────────────────
    rationale = _build_rationale(sa, pbs_result)

    # ── Create signal ────────────────────────────────────────────────────
    win_prob = confidence_to_win_probability(score)

    signal = TradeSignal(
        symbol=sa.symbol,
        direction=sa.trade_direction,
        entry_price=sa.entry_price,
        stop_loss=sa.stop_loss,
        take_profit=sa.take_profit,
        confidence=score,
        win_probability=win_prob,
        risk_reward_ratio=round(rr_ratio, 2),
        atr=sa.atr,
        spread_pips=sa.spread_pips,
        rationale=rationale,
        pristine_setup=pristine_label,
    )

    setup_tag = f"  [{pristine_label}]" if pristine_label else ""
    log.info(
        f"SIGNAL: {signal.direction} {signal.symbol} "
        f"@ {signal.entry_price:.5f}  SL={signal.stop_loss:.5f}  "
        f"TP={signal.take_profit:.5f}  R:R={signal.risk_reward_ratio}  "
        f"Conf={signal.confidence:.1f}  WinP={signal.win_probability:.2f}"
        f"{setup_tag}"
    )

    return signal


def _detect_pbs_pss(sa: SymbolAnalysis) -> dict | None:
    """
    Attempt to detect a formal Pristine Buy Setup or Pristine Sell Setup.
    Uses the higher-TF stage, trading-TF pivots, and entry-TF conditions.
    """
    direction_val = 1 if sa.trade_direction == "BUY" else -1

    # Get higher TF stage (D1 preferred)
    stage = {}
    for tf in ["D1", "H4"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.stage.get("confidence", 0) > stage.get("confidence", 0):
            stage = tfa.stage

    # Get H1 pivot trend and retracement
    h1 = sa.timeframes.get("H1")
    pivot_trend = h1.pivot_trend if h1 else {}
    retracement = h1.retracement if h1 else {}
    volume_class = h1.volume_class if h1 else {}

    # Sweet spot from the symbol analysis
    sweet_spot = sa.sweet_spot or {}

    # Last candle from entry TF
    entry_tfa = sa.timeframes.get("M15") or sa.timeframes.get("H1")
    last_candle = {}
    if entry_tfa and entry_tfa.candle_class:
        last_candle = entry_tfa.candle_class[-1]

    # S/R levels
    sr_levels = sa.multi_tf_sr or []
    if not sr_levels and entry_tfa:
        sr_levels = entry_tfa.sr_levels

    return detect_pristine_setup(
        stage=stage,
        pivot_trend=pivot_trend,
        retracement=retracement,
        volume_class=volume_class,
        sweet_spot=sweet_spot,
        last_candle=last_candle,
        sr_levels=sr_levels,
        current_price=sa.entry_price,
        direction=direction_val,
    )


def _build_rationale(sa: SymbolAnalysis, pbs: dict | None = None) -> list[str]:
    """Build a human-readable list of reasons for the trade."""
    reasons = []

    # ── Pristine Method info ─────────────────────────────────────────────
    # Stage info (Ch. 1)
    for tf in ["D1", "H4"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.stage:
            st = tfa.stage
            reasons.append(
                f"{tf} Stage {st.get('stage', '?')} "
                f"({st.get('description', '?')}) "
                f"[conf={st.get('confidence', 0):.0%}]"
            )
            break

    # Pivot trend (Ch. 10)
    h1 = sa.timeframes.get("H1")
    if h1 and h1.pivot_trend:
        pv = h1.pivot_trend
        reasons.append(
            f"H1 pivot trend: {pv.get('trend', '?')} "
            f"({pv.get('strength', '?')}) — "
            f"HPH={pv.get('hph_count', 0)} HPL={pv.get('hpl_count', 0)}"
        )

    # Retracement (Ch. 6)
    if h1 and h1.retracement:
        ret = h1.retracement
        reasons.append(
            f"H1 retracement: {ret.get('quality', '?')} "
            f"({ret.get('retracement_pct', 0):.0%}) "
            f"{'at 20 EMA' if ret.get('near_ma20') else ''}"
        )

    # Sweet spot (Ch. 12)
    if sa.sweet_spot:
        ss = sa.sweet_spot
        reasons.append(
            f"Multi-TF: {ss.get('type', '?')} "
            f"(score={ss.get('score', 0):.2f})"
        )

    # PBS/PSS info
    if pbs:
        reasons.append(
            f"Pristine Setup: {pbs['type']} {pbs['quality']} "
            f"({pbs.get('met_count', 0)}/{pbs.get('total_criteria', 7)} criteria)"
        )
        for c in pbs.get("criteria_met", [])[:3]:
            reasons.append(f"  ✓ {c}")
        for c in pbs.get("criteria_missed", [])[:2]:
            reasons.append(f"  ✗ {c}")

    # BBF signals (Ch. 13)
    if sa.bbf_signals:
        for bbf in sa.bbf_signals[:2]:
            reasons.append(f"BBF: {bbf.get('name', '')} (strength={bbf.get('strength', 0):.2f})")

    # ── Legacy info ──────────────────────────────────────────────────────
    reasons.append(f"Higher TF bias: {sa.higher_tf_bias}")
    reasons.append(f"Trading TF bias: {sa.trading_tf_bias}")

    # Key indicators (demoted but still informative)
    if h1 and h1.indicators:
        ind = h1.indicators
        reasons.append(
            f"H1 indicators: RSI={ind.get('rsi', 0):.1f} "
            f"ADX={ind.get('adx', 0):.1f} "
            f"ATR={ind.get('atr', 0):.5f}"
        )

    # Volume classification (Ch. 5)
    if h1 and h1.volume_class:
        vc = h1.volume_class
        reasons.append(
            f"Volume: type={vc.get('current_vol_type', '?')} "
            f"pullback={vc.get('pullback_vol_trend', '?')} "
            f"confirms={'yes' if vc.get('vol_confirms_trend') else 'no'}"
        )

    # Patterns
    for tf in ["M15", "H1"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.candle_patterns:
            names = [p["name"] for p in tfa.candle_patterns if p["bias"] != 0]
            if names:
                reasons.append(f"{tf} patterns: {', '.join(names)}")

    return reasons
