"""
===============================================================================
  GPT-5.2 Visual Chart Analyst — Two-Tier Chart Intelligence
===============================================================================
  Tier 1 (Visual):
    Render professional candlestick charts for M15/H1/H4/D1, send to GPT-5.2
    vision for UNBIASED technical analysis.  GPT does NOT know our trade
    direction — it just reports what it sees on the chart.
    Charts show candlesticks, volume, EMAs, but NO trade levels.

  Tier 2 (Contextual — VISUAL):
    RE-SEND the same charts, now annotated with Entry/SL/TP levels, together
    with the Tier 1 report and our signal parameters.  GPT can now see
    exactly where we plan to enter, stop out, and take profit, and assess
    whether those levels make geometric sense on the chart.
    Returns: alignment, risk_factor (0.5–1.0), red_flags, supports,
    and a new sl_tp_assessment section.

  Design principles:
    - Tier 1 is deliberately unbiased (no direction hint, no levels).
    - Tier 2 is a VISUAL risk assessment — it sees charts WITH our levels.
    - For marginal setups (score < 65), contradictory chart analysis with
      risk_factor < 0.6 results in a VETO (implemented in main.py).
    - For higher-confidence setups, risk_factor scales position size.
    - Trade still executes if GPT fails (default risk_factor = 1.0).
    - Total latency budget: ~30-50s (two vision calls + chart rendering).
    - Charts are rendered server-side with matplotlib (no GUI needed).
===============================================================================
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from core.mt5_connector import MT5Connector
from utils.logger import get_logger

log = get_logger("chart_analyst")

# ── Chart/analysis save directory ─────────────────────────────────────────────
# Structure: logs/chart_analysis/<SYMBOL>/<timestamp>/
#   clean_M15.png, clean_H1.png, ...      (Tier 1 — unbiased)
#   annotated_M15.png, annotated_H1.png, ... (Tier 2 — with Entry/SL/TP)
#   tier1_visual_report.txt                (GPT Tier 1 text)
#   tier2_risk_assessment.json             (GPT Tier 2 structured output)
#   signal_data.json                       (our signal parameters)
ANALYSIS_DIR: Path = cfg.LOG_DIR / "chart_analysis"


def _save_charts_to_disk(
    symbol: str,
    timestamp_str: str,
    charts: list[tuple[str, bytes]],
    prefix: str,
) -> Path:
    """
    Save chart images to disk.  Returns the directory they were saved in.
    prefix: "clean" for Tier 1 charts, "annotated" for Tier 2 charts.
    """
    save_dir = ANALYSIS_DIR / symbol / timestamp_str
    save_dir.mkdir(parents=True, exist_ok=True)
    for tf, png_bytes in charts:
        path = save_dir / f"{prefix}_{tf}.png"
        path.write_bytes(png_bytes)
    return save_dir


def _save_analysis_to_disk(
    save_dir: Path,
    chart_report: Optional[str],
    assessment: Optional[dict],
    signal_data: dict,
    score_breakdown: dict,
):
    """
    Save the GPT analysis text and structured assessment to disk.
    Everything needed to review the analysis later in one place.
    """
    save_dir.mkdir(parents=True, exist_ok=True)

    # Tier 1 visual report (raw GPT output)
    if chart_report:
        (save_dir / "tier1_visual_report.txt").write_text(
            chart_report, encoding="utf-8",
        )

    # Tier 2 structured assessment (JSON)
    if assessment:
        with open(save_dir / "tier2_risk_assessment.json", "w") as f:
            json.dump(assessment, f, indent=2, default=str)

    # Our signal data (for reproducibility)
    with open(save_dir / "signal_data.json", "w") as f:
        combined = {
            "signal": signal_data,
            "score_breakdown": score_breakdown,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        json.dump(combined, f, indent=2, default=str)


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


def _draw_trade_levels(ax, n_bars: int, levels: dict):
    """
    Draw Entry / SL / TP horizontal lines and risk/reward shaded zones.

    This makes GPT's job dramatically easier: it can see exactly where our
    stop sits relative to chart structure, whether our TP faces resistance,
    and whether the entry price is at a meaningful level.

    Colors:
      Entry  — bright white, solid
      SL     — red (#FF5252), dashed
      TP     — cyan (#00E5FF), dashed
      Risk zone (entry→SL)   — red-tinted transparent fill
      Reward zone (entry→TP) — green-tinted transparent fill
    """
    entry = levels.get("entry", 0)
    sl = levels.get("sl", 0)
    tp = levels.get("tp", 0)
    direction = levels.get("direction", "BUY")

    if entry <= 0:
        return

    # ── Entry line ────────────────────────────────────────────────────
    ax.axhline(y=entry, color="#FFFFFF", linestyle="-", linewidth=1.2, alpha=0.9)
    ax.annotate(
        f"  ENTRY {entry:.5g}",
        xy=(n_bars - 1, entry),
        color="#FFFFFF", fontsize=9, fontweight="bold", va="bottom",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#1B5E20",
                  edgecolor="#4CAF50", alpha=0.9),
    )

    # ── Stop Loss line ────────────────────────────────────────────────
    if sl > 0:
        ax.axhline(y=sl, color="#FF5252", linestyle="--", linewidth=1.4, alpha=0.9)
        ax.annotate(
            f"  SL {sl:.5g}",
            xy=(n_bars - 1, sl),
            color="#FFFFFF", fontsize=9, fontweight="bold",
            va="top" if sl < entry else "bottom",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#B71C1C",
                      edgecolor="#FF5252", alpha=0.9),
        )
        # Risk zone: entry to SL
        zone_lo = min(entry, sl)
        zone_hi = max(entry, sl)
        ax.axhspan(zone_lo, zone_hi, alpha=0.08, color="#FF5252")

    # ── Take Profit line ──────────────────────────────────────────────
    if tp > 0:
        ax.axhline(y=tp, color="#00E5FF", linestyle="--", linewidth=1.4, alpha=0.9)
        ax.annotate(
            f"  TP {tp:.5g}",
            xy=(n_bars - 1, tp),
            color="#FFFFFF", fontsize=9, fontweight="bold",
            va="bottom" if tp > entry else "top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#006064",
                      edgecolor="#00E5FF", alpha=0.9),
        )
        # Reward zone: entry to TP
        zone_lo = min(entry, tp)
        zone_hi = max(entry, tp)
        ax.axhspan(zone_lo, zone_hi, alpha=0.06, color="#00E676")

    # ── Direction arrow (small visual hint) ───────────────────────────
    arrow_y = entry
    arrow_dy = abs(entry - tp) * 0.15 if tp > 0 else abs(entry - sl) * 0.15
    if direction == "SELL":
        arrow_dy = -arrow_dy
    ax.annotate(
        "", xy=(n_bars - 3, arrow_y + arrow_dy),
        xytext=(n_bars - 3, arrow_y),
        arrowprops=dict(arrowstyle="->", color="#FFFFFF", lw=2),
    )


def render_chart(
    mt5_conn: MT5Connector,
    symbol: str,
    timeframe: str,
    current_price: float = 0.0,
    num_bars: int = 200,
    trade_levels: Optional[dict] = None,
) -> Optional[bytes]:
    """
    Render a professional candlestick chart with volume and EMAs.

    Returns PNG image as bytes, or None on failure.
    Chart uses a dark theme similar to TradingView for optimal GPT reading.

    trade_levels (optional):
        {"entry": float, "sl": float, "tp": float, "direction": str}
        When provided, horizontal lines and shaded risk/reward zones are
        drawn so GPT can visually assess the trade geometry.
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

    # ── Trade level annotations (Entry / SL / TP) ────────────────────────
    # Drawn ONLY for Tier 2 annotated charts.  Tier 1 charts are clean
    # (unbiased — GPT shouldn't know our direction during Tier 1).
    if trade_levels:
        _draw_trade_levels(ax_price, n, trade_levels)

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

    # ── Title (include "(ANNOTATED)" when trade levels are shown) ────────
    title_suffix = "  [ANNOTATED]" if trade_levels else ""
    ax_price.set_title(
        f"{symbol}  •  {timeframe}{title_suffix}",
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
    trade_levels: Optional[dict] = None,
) -> list[tuple[str, bytes]]:
    """
    Render charts for all analysis timeframes.
    Returns list of (timeframe, png_bytes) tuples.

    trade_levels: if provided, Entry/SL/TP are drawn on every chart.
                  Pass None for clean (Tier 1) charts.
    """
    charts = []
    for tf in CHART_TIMEFRAMES:
        num_bars = CHART_BARS.get(tf, 200)
        png = render_chart(
            mt5_conn, symbol, tf, current_price, num_bars,
            trade_levels=trade_levels,
        )
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
) -> tuple[Optional[str], list[tuple[str, bytes]]]:
    """
    Tier 1: Send CLEAN charts to GPT-5.2 for unbiased visual technical analysis.

    GPT does NOT know our trade direction — it produces a neutral report
    of what it sees on the charts: trend, structure, levels, patterns.
    Charts have NO trade levels annotated (Entry/SL/TP are hidden).

    Returns:
        (report_text, charts_list) — charts are returned so Tier 2 can
        re-render annotated versions without re-fetching data.
    """
    # Tier 1: clean charts — no trade levels
    charts = render_all_charts(mt5_conn, symbol, current_price, trade_levels=None)
    if len(charts) < 2:
        log.warning(f"{symbol}: could not render enough charts for analysis")
        return None, charts

    tf_list = ", ".join(tf for tf, _ in charts)
    prompt = _VISUAL_PROMPT_TEMPLATE.format(
        symbol=symbol,
        n_charts=len(charts),
        tf_list=tf_list,
        current_price=f"{current_price:.5g}",
    )

    images = [png for _, png in charts]
    report = _call_vision(images, _VISUAL_SYSTEM, prompt)
    return report, charts


# ═════════════════════════════════════════════════════════════════════════════
#  TIER 2: CONTEXTUAL RISK ASSESSMENT
# ═════════════════════════════════════════════════════════════════════════════

_CONTEXT_SYSTEM = """You are a senior risk manager at a quantitative trading desk.
You are reviewing ANNOTATED charts that show our proposed Entry (white line),
Stop Loss (red dashed line with red shaded zone), and Take Profit (cyan dashed
line with green shaded zone).
You also have an independent chart analysis (from an analyst who did NOT see
these levels) and our signal parameters.
Your job is to:
  1. Assess whether the chart supports or contradicts our trade direction.
  2. Evaluate whether our SL and TP are well-placed relative to visible
     chart structure (support/resistance, EMAs, swing points, volume nodes).
  3. Identify specific risks and confirmations.
Be precise and honest. Reference what you SEE on the annotated charts.
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

An independent visual chart analysis (analyst did NOT know our direction or levels) found:

---
{chart_report}
---

The ANNOTATED charts (attached) now show our Entry, SL, and TP levels.
Look at them carefully. Assess the following:

1. DIRECTIONAL ALIGNMENT: Does the multi-TF chart structure SUPPORT or CONTRADICT a {direction} trade?
2. STOP LOSS ASSESSMENT: Look at where the red SL line sits on the chart.
   - Is it behind a meaningful structural level (swing low/high, S/R zone, EMA)?
   - Or is it in "no man's land" where it could easily be swept before the real move?
   - Would a different SL placement be safer? (just note, don't change our levels)
3. TAKE PROFIT ASSESSMENT: Look at where the cyan TP line sits.
   - Does price have a clear path to reach TP, or is there a major S/R zone in the way?
   - Is the TP realistic given the current market structure?
4. ENTRY TIMING: Is the entry price at a meaningful location (EMA, S/R, after pullback)?
5. RISK FACTORS: Any specific threats visible on the charts?
6. RISK FACTOR: 0.5 to 1.0
   - 1.0 = charts strongly support the trade, SL/TP are well-placed
   - 0.85 = generally supportive with minor concerns
   - 0.7 = some chart concerns or suboptimal level placement
   - 0.5 = significant structural contradictions or dangerously placed SL/TP

Reply with ONLY this JSON:
{{
  "alignment": "supportive|neutral|contradictory",
  "risk_factor": <float>,
  "red_flags": ["<specific chart-based concerns>"],
  "supports": ["<specific chart-based confirmations>"],
  "sl_assessment": "<1-2 sentences on SL placement quality>",
  "tp_assessment": "<1-2 sentences on TP placement quality>",
  "reasoning": "<2-3 sentences overall>"
}}"""


def _parse_assessment_json(text: str) -> Optional[dict]:
    """Parse the JSON response from Tier 2, handling markdown fences."""
    try:
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
        result.setdefault("sl_assessment", "")
        result.setdefault("tp_assessment", "")

        return result
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.warning(f"Failed to parse contextual assessment: {e} — text: {text[:300]}")
        return None


def contextual_assessment(
    chart_report: str,
    signal_data: dict,
    score_breakdown: dict,
    annotated_images: Optional[list[bytes]] = None,
) -> Optional[dict]:
    """
    Tier 2: VISUAL contextual risk assessment.

    When annotated_images are provided, GPT sees the charts with Entry/SL/TP
    drawn on them — enabling it to assess whether our levels make geometric
    sense relative to chart structure.

    Falls back to text-only analysis if no images are provided.

    Returns dict with:
        alignment: "supportive" / "neutral" / "contradictory"
        risk_factor: float 0.5-1.0
        red_flags: list[str]
        supports: list[str]
        sl_assessment: str
        tp_assessment: str
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

    # ── Prefer vision call when annotated charts are available ─────────
    if annotated_images:
        log.info(
            f"Tier 2: visual assessment with {len(annotated_images)} "
            "annotated charts (Entry/SL/TP visible)"
        )
        text = _call_vision(
            annotated_images, _CONTEXT_SYSTEM, prompt,
            max_tokens=1536,
        )
    else:
        log.info("Tier 2: text-only assessment (no annotated charts)")
        text = _call_text(_CONTEXT_SYSTEM, prompt)

    if text is None:
        return None

    return _parse_assessment_json(text)


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

    Pipeline:
      1. Render CLEAN charts (no trade levels) → Tier 1 vision call
         (unbiased: GPT doesn't know our direction).
      2. Render ANNOTATED charts (Entry/SL/TP drawn) → Tier 2 vision call
         (contextual: GPT sees our exact levels on the chart and assesses
         whether they make geometric sense relative to chart structure).

    Returns:
        {
            "chart_report": str | None,     # Tier 1 visual analysis
            "risk_assessment": dict | None,  # Tier 2 contextual assessment
            "risk_factor": float,            # 0.5-1.0 (1.0 = no reduction)
            "red_flags": list[str],
            "supports": list[str],
            "alignment": str,
            "sl_assessment": str,            # GPT's view on SL placement
            "tp_assessment": str,            # GPT's view on TP placement
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
        "sl_assessment": "",
        "tp_assessment": "",
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

    # Timestamp for file persistence (unique per analysis run)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # ── Tier 1: Visual analysis (unbiased — clean charts) ────────────────
    log.info(f"{symbol}: starting visual chart analysis (Tier 1 — clean charts)...")
    chart_report, clean_charts = visual_analysis(mt5_conn, symbol, current_price)

    # Save clean charts to disk (even if analysis fails, charts are useful)
    save_dir = None
    if clean_charts:
        try:
            save_dir = _save_charts_to_disk(symbol, ts_str, clean_charts, "clean")
            log.info(f"{symbol}: saved {len(clean_charts)} clean charts → {save_dir}")
        except Exception as e:
            log.warning(f"{symbol}: failed to save clean charts: {e}")

    if chart_report is None:
        log.warning(f"{symbol}: visual analysis failed — proceeding with default risk")
        default_result["elapsed_seconds"] = time.time() - t0
        return default_result

    log.info(f"{symbol}: visual analysis complete ({len(chart_report)} chars)")

    # ── Render ANNOTATED charts for Tier 2 ───────────────────────────────
    # These charts have Entry/SL/TP lines and risk/reward shaded zones
    # drawn on them, so GPT can assess the geometry of our trade levels.
    trade_levels = {
        "entry": signal_data.get("entry_price", 0),
        "sl": signal_data.get("stop_loss", 0),
        "tp": signal_data.get("take_profit", 0),
        "direction": signal_data.get("direction", "BUY"),
    }
    annotated_charts = render_all_charts(
        mt5_conn, symbol, current_price, trade_levels=trade_levels,
    )
    annotated_images = [png for _, png in annotated_charts] if annotated_charts else None

    # Save annotated charts to disk
    if annotated_charts and save_dir:
        try:
            _save_charts_to_disk(symbol, ts_str, annotated_charts, "annotated")
            log.info(
                f"{symbol}: saved {len(annotated_charts)} annotated charts "
                f"(Entry/SL/TP visible) → {save_dir}"
            )
        except Exception as e:
            log.warning(f"{symbol}: failed to save annotated charts: {e}")
    elif annotated_images:
        log.info(
            f"{symbol}: rendered {len(annotated_images)} annotated charts "
            f"(Entry/SL/TP visible) for Tier 2"
        )

    # ── Tier 2: Contextual assessment (VISUAL — annotated charts) ────────
    log.info(f"{symbol}: starting contextual risk assessment (Tier 2 — annotated charts)...")
    assessment = contextual_assessment(
        chart_report, signal_data, score_breakdown,
        annotated_images=annotated_images,
    )

    elapsed = time.time() - t0

    # ── Save analysis to disk (Tier 1 report + Tier 2 JSON + signal) ─────
    if save_dir:
        try:
            _save_analysis_to_disk(
                save_dir, chart_report, assessment,
                signal_data, score_breakdown,
            )
            log.info(f"{symbol}: saved analysis text + assessment → {save_dir}")
        except Exception as e:
            log.warning(f"{symbol}: failed to save analysis: {e}")

    if assessment is None:
        log.warning(f"{symbol}: contextual assessment failed — using default risk")
        return {
            "chart_report": chart_report,
            "risk_assessment": None,
            "risk_factor": 0.85,  # slightly cautious if we can't assess
            "red_flags": [],
            "supports": [],
            "alignment": "unassessed",
            "sl_assessment": "",
            "tp_assessment": "",
            "elapsed_seconds": elapsed,
            "analysis_dir": str(save_dir) if save_dir else "",
        }

    risk_factor = assessment["risk_factor"]
    alignment = assessment["alignment"]
    red_flags = assessment.get("red_flags", [])
    supports = assessment.get("supports", [])
    reasoning = assessment.get("reasoning", "")
    sl_assessment = assessment.get("sl_assessment", "")
    tp_assessment = assessment.get("tp_assessment", "")

    log.info(
        f"{symbol}: chart analysis complete in {elapsed:.1f}s — "
        f"alignment={alignment} risk_factor={risk_factor:.2f} "
        f"red_flags={len(red_flags)} supports={len(supports)}"
    )
    if sl_assessment:
        log.info(f"{symbol}: SL assessment: {sl_assessment}")
    if tp_assessment:
        log.info(f"{symbol}: TP assessment: {tp_assessment}")
    if reasoning:
        log.info(f"{symbol}: GPT reasoning: {reasoning}")

    return {
        "chart_report": chart_report,
        "risk_assessment": assessment,
        "risk_factor": risk_factor,
        "red_flags": red_flags,
        "supports": supports,
        "alignment": alignment,
        "sl_assessment": sl_assessment,
        "tp_assessment": tp_assessment,
        "elapsed_seconds": elapsed,
        "analysis_dir": str(save_dir) if save_dir else "",
    }
