"""
===============================================================================
  GPT-5.2 Visual Chart Analyst — Two-Tier Chart Intelligence
===============================================================================
  Tier 1 (Visual):
    Render professional candlestick charts for M15/H1/H4/D1, send to GPT-5.2
    vision for UNBIASED technical analysis.  GPT does NOT know our trade
    direction — it just reports what it sees on the chart.

  Tier 2 (Contextual):
    Compare the unbiased visual analysis against our proposed trade signal.
    GPT now knows the direction and assesses alignment, red flags, and
    produces a risk_factor (0.5–1.0) that scales position size.

  Design principles:
    - AI does NOT veto trades.  It adjusts risk sizing.
    - Tier 1 is deliberately unbiased (no direction hint).
    - Tier 2 is a structured risk assessment.
    - Total latency budget: ~15-25s.  Trade still executes if GPT fails.
    - Charts are rendered server-side with matplotlib (no GUI needed).
===============================================================================
"""

from __future__ import annotations

import base64
import io
import json
import time
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from core.mt5_connector import MT5Connector
from utils.logger import get_logger

log = get_logger("chart_analyst")

# ── Lazy imports for heavyweight packages ────────────────────────────────────
_matplotlib_ready = False


def _ensure_matplotlib():
    """Import and configure matplotlib (non-interactive backend)."""
    global _matplotlib_ready
    if _matplotlib_ready:
        return True
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless rendering
        _matplotlib_ready = True
        return True
    except ImportError:
        log.error("matplotlib not installed — chart analysis disabled")
        return False


def _get_openai_client():
    """Reuse the same lazy client from ai_analyst."""
    from core.ai_analyst import _get_client
    return _get_client()


# ═════════════════════════════════════════════════════════════════════════════
#  CHART RENDERING
# ═════════════════════════════════════════════════════════════════════════════

# Timeframes to chart (entry → macro)
CHART_TIMEFRAMES = ["M15", "H1", "H4", "D1"]

# Bars per chart (tuned for readability in GPT vision)
CHART_BARS = {
    "M15": 200,   # ~50 hours ≈ 2 trading days
    "H1":  200,   # ~8 trading days
    "H4":  200,   # ~33 trading days
    "D1":  250,   # ~1 year
}


def render_chart(
    mt5_conn: MT5Connector,
    symbol: str,
    timeframe: str,
    current_price: float = 0.0,
    num_bars: int = 200,
) -> Optional[bytes]:
    """
    Render a professional candlestick chart with volume and EMAs.

    Returns PNG image as bytes, or None on failure.
    Chart uses a dark theme similar to TradingView for optimal GPT reading.
    """
    if not _ensure_matplotlib():
        return None

    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    # Fetch data
    df = mt5_conn.get_rates(symbol, timeframe, count=num_bars + 200)
    if df is None or len(df) < 50:
        log.warning(f"Insufficient data for {symbol} {timeframe} chart")
        return None

    # Calculate EMAs on full data, then trim for display
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    has_ema200 = len(df) >= 220
    if has_ema200:
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    # Trim to display range (keep last num_bars)
    df = df.tail(num_bars).copy()
    df.reset_index(inplace=True)
    n = len(df)
    x = np.arange(n)

    # ── Create figure ────────────────────────────────────────────────────
    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1, figsize=(16, 10),
        gridspec_kw={"height_ratios": [4, 1]},
        sharex=True,
    )
    fig.patch.set_facecolor("#131722")

    # ── Draw candlesticks ────────────────────────────────────────────────
    for i in range(n):
        row = df.iloc[i]
        is_up = row["close"] >= row["open"]
        color = "#26a69a" if is_up else "#ef5350"

        # Wick (high-low line)
        ax_price.vlines(x[i], row["low"], row["high"], color=color, linewidth=0.6)

        # Body
        body_lo = min(row["open"], row["close"])
        body_hi = max(row["open"], row["close"])
        body_h = body_hi - body_lo

        if body_h < (row["high"] - row["low"]) * 0.005:
            # Doji — just a horizontal tick
            ax_price.hlines(row["close"], x[i] - 0.35, x[i] + 0.35,
                            color=color, linewidth=1)
        else:
            rect = Rectangle(
                (x[i] - 0.35, body_lo), 0.7, body_h,
                facecolor=color, edgecolor=color, linewidth=0.5,
            )
            ax_price.add_patch(rect)

    # ── EMAs ─────────────────────────────────────────────────────────────
    ax_price.plot(x, df["ema20"].values, color="#FF9800", linewidth=1.2,
                  label="EMA 20", alpha=0.8)
    ax_price.plot(x, df["ema50"].values, color="#2196F3", linewidth=1.2,
                  label="EMA 50", alpha=0.8)
    if has_ema200 and "ema200" in df.columns:
        ax_price.plot(x, df["ema200"].values, color="#AB47BC", linewidth=1.2,
                      label="EMA 200", alpha=0.8)

    # ── Current price line ───────────────────────────────────────────────
    if current_price > 0:
        ax_price.axhline(y=current_price, color="#FFFFFF", linestyle="--",
                         linewidth=0.7, alpha=0.4)
        ax_price.annotate(
            f"  {current_price:.5g}", xy=(n - 1, current_price),
            color="white", fontsize=8, va="center",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#131722",
                      edgecolor="#787B86", alpha=0.8),
        )

    # ── Volume bars ──────────────────────────────────────────────────────
    vol_colors = [
        "#26a69a80" if df.iloc[i]["close"] >= df.iloc[i]["open"] else "#ef535080"
        for i in range(n)
    ]
    ax_vol.bar(x, df["volume"].values, color=vol_colors, width=0.7)

    # ── Styling ──────────────────────────────────────────────────────────
    for ax in [ax_price, ax_vol]:
        ax.set_facecolor("#131722")
        ax.tick_params(colors="#787B86", labelsize=8)
        ax.grid(True, alpha=0.08, color="#787B86")
        for spine in ax.spines.values():
            spine.set_color("#363A45")

    ax_price.set_title(
        f"{symbol}  •  {timeframe}",
        color="#D1D4DC", fontsize=14, fontweight="bold", pad=10,
    )
    ax_price.legend(
        loc="upper left", fontsize=8,
        facecolor="#131722", edgecolor="#787B86", labelcolor="#D1D4DC",
    )
    ax_price.yaxis.set_label_position("right")
    ax_price.yaxis.tick_right()
    ax_vol.yaxis.set_label_position("right")
    ax_vol.yaxis.tick_right()

    # ── X-axis date labels ───────────────────────────────────────────────
    n_labels = min(12, n)
    step = max(1, n // n_labels)
    tick_positions = list(range(0, n, step))

    if timeframe in ("M15", "H1"):
        fmt = "%m/%d %H:%M"
    elif timeframe in ("H4",):
        fmt = "%m/%d %H:%M"
    else:
        fmt = "%Y-%m-%d"

    tick_labels = []
    for pos in tick_positions:
        ts = df.iloc[pos]["time"]
        if hasattr(ts, "strftime"):
            tick_labels.append(ts.strftime(fmt))
        else:
            tick_labels.append(str(ts)[:16])

    ax_vol.set_xticks(tick_positions)
    ax_vol.set_xticklabels(tick_labels, rotation=45, ha="right",
                           fontsize=7, color="#787B86")

    plt.tight_layout()

    # ── Save to bytes ────────────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_all_charts(
    mt5_conn: MT5Connector,
    symbol: str,
    current_price: float = 0.0,
) -> list[tuple[str, bytes]]:
    """
    Render charts for all analysis timeframes.
    Returns list of (timeframe, png_bytes) tuples.
    """
    charts = []
    for tf in CHART_TIMEFRAMES:
        num_bars = CHART_BARS.get(tf, 200)
        png = render_chart(mt5_conn, symbol, tf, current_price, num_bars)
        if png:
            charts.append((tf, png))
        else:
            log.warning(f"Failed to render {symbol} {tf} chart — skipping")
    return charts


# ═════════════════════════════════════════════════════════════════════════════
#  TIER 1: UNBIASED VISUAL ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

_VISUAL_SYSTEM = """You are a professional multi-timeframe technical analyst.
You analyze candlestick charts with surgical precision.
You report ONLY what you see — no hallucination, no speculation.
If something is unclear on the chart, say so explicitly.
Treat this as real money analysis for an institutional desk."""

_VISUAL_PROMPT_TEMPLATE = """Analyze {symbol} across {n_charts} timeframes.
Charts provided (in order): {tf_list}
Each chart shows: candlesticks, volume bars, EMA 20 (orange), EMA 50 (blue), EMA 200 (purple).
Current price: {current_price}

Follow this EXACT structure:

A) MARKET REGIME (from D1 and H4):
- Trend direction: up / down / range (define by HH/HL vs LH/LL structure)
- Market stage: accumulation, markup, distribution, or markdown
- EMA alignment: are 20/50/200 EMAs in bullish or bearish order?
- What would flip the current bias?

B) KEY LEVELS (visible on the charts):
- Major support zones (at least 2)
- Major resistance zones (at least 2)
- Any psychological round-number levels nearby
- Supply/demand zones if visible

C) CURRENT POSITION:
- Where is price relative to the EMAs?
- Premium or discount within the visible range?
- Any visible candlestick patterns at current location? (engulfing, pin bar, doji, etc.)
- Volume behavior: increasing/decreasing, does it confirm price?

D) PATTERN QUALITY:
- Any multi-candle reversal or continuation patterns?
- Breakout/breakdown setups with volume confirmation?
- Signs of exhaustion or strong momentum?
- Base/consolidation patterns before expansion?

E) RISK FACTORS:
- What levels could stop a bullish move?
- What levels could stop a bearish move?
- Any divergences between price and volume?
- Any trapped traders (false breakouts, sweeps)?

Be specific. Reference bar positions (e.g. "the last 5 candles show...").
Do NOT make trade recommendations or give entry/exit levels.
Report your analysis only."""


def _call_vision(
    images: list[bytes],
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
) -> Optional[str]:
    """Call GPT-5.2 vision with multiple chart images."""
    client = _get_openai_client()
    if client is None:
        return None

    # Build content array: text + images
    content = [{"type": "text", "text": user_prompt}]
    for img_bytes in images:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64}",
                "detail": "high",
            },
        })

    try:
        t0 = time.time()
        resp = client.chat.completions.create(
            model=cfg.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            max_completion_tokens=max_tokens,
            temperature=0.2,
        )
        elapsed = time.time() - t0
        result = resp.choices[0].message.content.strip()
        log.info(f"GPT vision call completed in {elapsed:.1f}s "
                 f"({len(result)} chars)")
        return result
    except Exception as e:
        log.error(f"GPT vision call failed: {e}")
        return None


def _call_text(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
) -> Optional[str]:
    """Call GPT-5.2 text-only for contextual analysis."""
    client = _get_openai_client()
    if client is None:
        return None

    try:
        t0 = time.time()
        resp = client.chat.completions.create(
            model=cfg.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_completion_tokens=max_tokens,
            temperature=0.2,
        )
        elapsed = time.time() - t0
        result = resp.choices[0].message.content.strip()
        log.info(f"GPT text call completed in {elapsed:.1f}s")
        return result
    except Exception as e:
        log.error(f"GPT text call failed: {e}")
        return None


def visual_analysis(
    mt5_conn: MT5Connector,
    symbol: str,
    current_price: float,
) -> Optional[str]:
    """
    Tier 1: Send charts to GPT-5.2 for unbiased visual technical analysis.

    GPT does NOT know our trade direction — it produces a neutral report
    of what it sees on the charts: trend, structure, levels, patterns.
    """
    charts = render_all_charts(mt5_conn, symbol, current_price)
    if len(charts) < 2:
        log.warning(f"{symbol}: could not render enough charts for analysis")
        return None

    tf_list = ", ".join(tf for tf, _ in charts)
    prompt = _VISUAL_PROMPT_TEMPLATE.format(
        symbol=symbol,
        n_charts=len(charts),
        tf_list=tf_list,
        current_price=f"{current_price:.5g}",
    )

    images = [png for _, png in charts]
    return _call_vision(images, _VISUAL_SYSTEM, prompt)


# ═════════════════════════════════════════════════════════════════════════════
#  TIER 2: CONTEXTUAL RISK ASSESSMENT
# ═════════════════════════════════════════════════════════════════════════════

_CONTEXT_SYSTEM = """You are a senior risk manager at a quantitative trading desk.
You compare an independent chart analysis against a proposed trade signal.
Your job is to assess alignment and identify specific risks.
Be precise and honest. If the chart supports the trade, say so.
If there are concerns, specify exactly what they are.
Reply with valid JSON only — no markdown, no commentary outside JSON."""

_CONTEXT_PROMPT_TEMPLATE = """Our algorithmic system generated this trade signal:

Symbol: {symbol}
Direction: {direction}
Entry Price: {entry_price}
Stop Loss: {stop_loss}
Take Profit: {take_profit}
Risk:Reward: 1:{risk_reward}
Confidence Score: {confidence}
Admission: {admission_type}

Score Breakdown:
  Stage alignment: {stage:.0%}
  Sweet spot (MTF): {sweet:.0%}
  Retracement: {retrace:.0%}
  S/R quality: {sr:.0%}
  Pivot trend: {pivot:.0%}
  Candle signal: {candle:.0%}
  Volume: {volume:.0%}
  Indicators: {ind:.0%}

An independent visual chart analysis (analyst did NOT know our direction) found:

---
{chart_report}
---

Based on the chart analysis vs our signal:

1. Does the visual analysis SUPPORT or CONTRADICT a {direction} trade?
2. Are there specific chart patterns or levels that threaten this trade?
3. What confirms the trade thesis from the charts?
4. Risk factor: 0.5 to 1.0 (1.0 = charts fully support, 0.5 = significant chart concerns)

Reply with ONLY this JSON:
{{"alignment": "supportive|neutral|contradictory", "risk_factor": <float>, "red_flags": ["..."], "supports": ["..."], "reasoning": "<2-3 sentences>"}}"""


def contextual_assessment(
    chart_report: str,
    signal_data: dict,
    score_breakdown: dict,
) -> Optional[dict]:
    """
    Tier 2: Compare the unbiased chart analysis against our trade signal.

    Returns dict with:
        alignment: "supportive" / "neutral" / "contradictory"
        risk_factor: float 0.5-1.0
        red_flags: list[str]
        supports: list[str]
        reasoning: str
    """
    bd = score_breakdown or {}
    admission = "auto-accept (≥75)" if signal_data.get("confidence", 0) >= 75 else \
                "standard review (65-75)" if signal_data.get("confidence", 0) >= 65 else \
                "structural override (55-65)"

    prompt = _CONTEXT_PROMPT_TEMPLATE.format(
        symbol=signal_data.get("symbol", "?"),
        direction=signal_data.get("direction", "?"),
        entry_price=signal_data.get("entry_price", 0),
        stop_loss=signal_data.get("stop_loss", 0),
        take_profit=signal_data.get("take_profit", 0),
        risk_reward=signal_data.get("risk_reward_ratio", 0),
        confidence=signal_data.get("confidence", 0),
        admission_type=admission,
        stage=bd.get("stage", 0),
        sweet=bd.get("sweet_spot", 0),
        retrace=bd.get("retracement", 0),
        sr=bd.get("sr_quality", 0),
        pivot=bd.get("pivot", 0),
        candle=bd.get("candle", 0),
        volume=bd.get("volume", 0),
        ind=bd.get("indicators", 0),
        chart_report=chart_report,
    )

    text = _call_text(_CONTEXT_SYSTEM, prompt)
    if text is None:
        return None

    # Parse JSON
    try:
        # Strip any markdown code fences
        cleaned = text
        if "```" in cleaned:
            parts = cleaned.split("```")
            for part in parts:
                stripped = part.strip()
                if stripped.startswith("json"):
                    stripped = stripped[4:].strip()
                if stripped.startswith("{"):
                    cleaned = stripped
                    break

        result = json.loads(cleaned)

        # Validate and clamp risk_factor
        rf = float(result.get("risk_factor", 0.75))
        result["risk_factor"] = max(0.5, min(1.0, rf))

        # Ensure required fields
        result.setdefault("alignment", "neutral")
        result.setdefault("red_flags", [])
        result.setdefault("supports", [])
        result.setdefault("reasoning", "")

        return result
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.warning(f"Failed to parse contextual assessment: {e} — text: {text[:300]}")
        return None


# ═════════════════════════════════════════════════════════════════════════════
#  FULL PIPELINE
# ═════════════════════════════════════════════════════════════════════════════

def analyze_signal_charts(
    mt5_conn: MT5Connector,
    signal_data: dict,
    score_breakdown: dict,
) -> dict:
    """
    Full two-tier chart analysis pipeline.

    Called after a signal passes all gates but before execution.
    Returns a result dict that ALWAYS has a risk_factor (defaults to 1.0
    on any failure, so trades are never blocked by chart analysis issues).

    Returns:
        {
            "chart_report": str | None,     # Tier 1 visual analysis
            "risk_assessment": dict | None,  # Tier 2 contextual assessment
            "risk_factor": float,            # 0.5-1.0 (1.0 = no reduction)
            "red_flags": list[str],
            "supports": list[str],
            "alignment": str,
            "elapsed_seconds": float,
        }
    """
    default_result = {
        "chart_report": None,
        "risk_assessment": None,
        "risk_factor": 1.0,
        "red_flags": [],
        "supports": [],
        "alignment": "unavailable",
        "elapsed_seconds": 0.0,
    }

    if not cfg.CHART_ANALYSIS_ENABLED:
        return default_result

    if not cfg.OPENAI_API_KEY:
        log.debug("Chart analysis skipped — no OpenAI API key")
        return default_result

    symbol = signal_data.get("symbol", "")
    current_price = signal_data.get("entry_price", 0)
    t0 = time.time()

    # ── Tier 1: Visual analysis (unbiased) ───────────────────────────────
    log.info(f"{symbol}: starting visual chart analysis (Tier 1)...")
    chart_report = visual_analysis(mt5_conn, symbol, current_price)

    if chart_report is None:
        log.warning(f"{symbol}: visual analysis failed — proceeding with default risk")
        default_result["elapsed_seconds"] = time.time() - t0
        return default_result

    log.info(f"{symbol}: visual analysis complete ({len(chart_report)} chars)")

    # ── Tier 2: Contextual assessment ────────────────────────────────────
    log.info(f"{symbol}: starting contextual risk assessment (Tier 2)...")
    assessment = contextual_assessment(chart_report, signal_data, score_breakdown)

    elapsed = time.time() - t0

    if assessment is None:
        log.warning(f"{symbol}: contextual assessment failed — using default risk")
        return {
            "chart_report": chart_report,
            "risk_assessment": None,
            "risk_factor": 0.85,  # slightly cautious if we can't assess
            "red_flags": [],
            "supports": [],
            "alignment": "unassessed",
            "elapsed_seconds": elapsed,
        }

    risk_factor = assessment["risk_factor"]
    alignment = assessment["alignment"]
    red_flags = assessment.get("red_flags", [])
    supports = assessment.get("supports", [])
    reasoning = assessment.get("reasoning", "")

    log.info(
        f"{symbol}: chart analysis complete in {elapsed:.1f}s — "
        f"alignment={alignment} risk_factor={risk_factor:.2f} "
        f"red_flags={len(red_flags)} supports={len(supports)}"
    )
    if reasoning:
        log.info(f"{symbol}: GPT reasoning: {reasoning}")

    return {
        "chart_report": chart_report,
        "risk_assessment": assessment,
        "risk_factor": risk_factor,
        "red_flags": red_flags,
        "supports": supports,
        "alignment": alignment,
        "elapsed_seconds": elapsed,
    }
