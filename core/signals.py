"""
===============================================================================
  Signal Generator — produces actionable trade signals from analysis
===============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import config as cfg
from core.confluence import SymbolAnalysis, compute_confluence_score
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

    # ── Gate 2: Compute confluence score ─────────────────────────────────
    score = compute_confluence_score(sa)
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

    # ── Build rationale ──────────────────────────────────────────────────
    rationale = _build_rationale(sa)

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
    )

    log.info(
        f"SIGNAL: {signal.direction} {signal.symbol} "
        f"@ {signal.entry_price:.5f}  SL={signal.stop_loss:.5f}  "
        f"TP={signal.take_profit:.5f}  R:R={signal.risk_reward_ratio}  "
        f"Conf={signal.confidence:.1f}  WinP={signal.win_probability:.2f}"
    )

    return signal


def _build_rationale(sa: SymbolAnalysis) -> list[str]:
    """Build a human-readable list of reasons for the trade."""
    reasons = []

    reasons.append(f"Higher TF bias: {sa.higher_tf_bias}")
    reasons.append(f"Trading TF bias: {sa.trading_tf_bias}")
    reasons.append(f"Entry TF bias: {sa.entry_tf_bias}")

    # Trend info
    for tf in ["D1", "H4", "H1"]:
        tfa = sa.timeframes.get(tf)
        if tfa:
            reasons.append(f"{tf}: trend={tfa.trend} structure={tfa.structure}")

    # Key indicators
    h1 = sa.timeframes.get("H1")
    if h1 and h1.indicators:
        ind = h1.indicators
        reasons.append(
            f"H1 indicators: RSI={ind.get('rsi', 0):.1f} "
            f"MACD_hist={ind.get('macd_hist', 0):.6f} "
            f"ADX={ind.get('adx', 0):.1f}"
        )

    # Patterns
    for tf in ["M15", "H1"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.candle_patterns:
            names = [p["name"] for p in tfa.candle_patterns if p["bias"] != 0]
            if names:
                reasons.append(f"{tf} patterns: {', '.join(names)}")

    # Smart money
    for tf in ["H1", "M15"]:
        tfa = sa.timeframes.get(tf)
        if tfa and tfa.smart_money:
            sm = tfa.smart_money
            if sm.get("liquidity_sweeps"):
                reasons.append(f"{tf}: liquidity sweep detected")
            if sm.get("structure_breaks"):
                for brk in sm["structure_breaks"]:
                    reasons.append(f"{tf}: {brk['type']}")

    return reasons
