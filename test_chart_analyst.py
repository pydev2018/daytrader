"""
Quick test: Run the chart analyst pipeline on live data.
Usage: conda activate tradebot && python test_chart_analyst.py
"""

import json
import time
import sys
import os

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

import config as cfg
from core.mt5_connector import MT5Connector
from core.chart_analyst import (
    render_chart,
    render_all_charts,
    visual_analysis,
    contextual_assessment,
    analyze_signal_charts,
)
from utils.logger import setup_logging

log = setup_logging("test_chart")

# ── Connect to MT5 ──────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  CHART ANALYST — LIVE TEST")
print("=" * 70)

mt5 = MT5Connector()
if not mt5.connect():
    print("FATAL: Cannot connect to MT5")
    sys.exit(1)
print("✓ MT5 connected")

# ── Pick test symbol ─────────────────────────────────────────────────────────
# Test with WHEAT (the missed opportunity) and one forex pair
TEST_SYMBOLS = ["WHEAT", "AUDJPY"]

for symbol in TEST_SYMBOLS:
    print(f"\n{'─' * 70}")
    print(f"  Testing: {symbol}")
    print(f"{'─' * 70}")

    # Get current price
    tick = mt5.symbol_tick(symbol)
    if tick is None:
        print(f"  ✗ Cannot get tick for {symbol} — skipping")
        continue
    current_price = (tick["bid"] + tick["ask"]) / 2
    print(f"  Current price: {current_price:.5g}")

    # ── Step 1: Render charts ────────────────────────────────────────────
    print(f"\n  [1/4] Rendering charts...")
    t0 = time.time()
    charts = render_all_charts(mt5, symbol, current_price)
    render_time = time.time() - t0
    print(f"  ✓ Rendered {len(charts)} charts in {render_time:.1f}s")

    # Save charts to disk for inspection
    chart_dir = os.path.join(os.path.dirname(__file__), "logs", "charts")
    os.makedirs(chart_dir, exist_ok=True)
    for tf, png_bytes in charts:
        chart_path = os.path.join(chart_dir, f"{symbol}_{tf}.png")
        with open(chart_path, "wb") as f:
            f.write(png_bytes)
        size_kb = len(png_bytes) / 1024
        print(f"    → {tf}: {size_kb:.0f} KB → {chart_path}")

    # ── Step 2: Visual analysis (Tier 1) ─────────────────────────────────
    print(f"\n  [2/4] Tier 1: Visual analysis (GPT-5.2 vision)...")
    t0 = time.time()
    chart_report = visual_analysis(mt5, symbol, current_price)
    vision_time = time.time() - t0

    if chart_report:
        print(f"  ✓ Visual analysis complete in {vision_time:.1f}s ({len(chart_report)} chars)")
        print(f"\n  {'─' * 60}")
        print(f"  VISUAL ANALYSIS REPORT:")
        print(f"  {'─' * 60}")
        # Print report with indentation
        for line in chart_report.split("\n"):
            print(f"  {line}")
    else:
        print(f"  ✗ Visual analysis failed after {vision_time:.1f}s")
        continue

    # ── Step 3: Contextual assessment (Tier 2) ───────────────────────────
    # Create a mock signal for testing
    mock_signal = {
        "symbol": symbol,
        "direction": "BUY",
        "entry_price": current_price,
        "stop_loss": current_price * 0.99,    # ~1% below
        "take_profit": current_price * 1.02,   # ~2% above
        "risk_reward_ratio": 2.0,
        "confidence": 62.0,
        "review_band": True,
    }

    # Mock score breakdown (from actual log data)
    if symbol == "WHEAT":
        mock_breakdown = {
            "stage": 0.90,
            "pivot": 0.70,
            "sweet_spot": 1.00,
            "sr_quality": 0.16,
            "retracement": 1.00,
            "candle": 0.69,
            "volume": 1.00,
            "indicators": 0.67,
        }
    else:  # AUDJPY
        mock_breakdown = {
            "stage": 1.00,
            "pivot": 0.15,
            "sweet_spot": 0.30,
            "sr_quality": 1.00,
            "retracement": 0.00,
            "candle": 1.00,
            "volume": 0.00,
            "indicators": 0.67,
        }

    print(f"\n  [3/4] Tier 2: Contextual assessment...")
    print(f"    Mock signal: {mock_signal['direction']} {symbol} "
          f"@ {mock_signal['entry_price']:.5g}")
    t0 = time.time()
    assessment = contextual_assessment(chart_report, mock_signal, mock_breakdown)
    context_time = time.time() - t0

    if assessment:
        print(f"  ✓ Assessment complete in {context_time:.1f}s")
        print(f"\n  {'─' * 60}")
        print(f"  CONTEXTUAL RISK ASSESSMENT:")
        print(f"  {'─' * 60}")
        print(f"  Alignment:   {assessment.get('alignment', '?')}")
        print(f"  Risk Factor: {assessment.get('risk_factor', '?')}")
        print(f"  Reasoning:   {assessment.get('reasoning', '?')}")
        print(f"  Red Flags:")
        for flag in assessment.get("red_flags", []):
            print(f"    ⚠ {flag}")
        print(f"  Supports:")
        for sup in assessment.get("supports", []):
            print(f"    ✓ {sup}")
    else:
        print(f"  ✗ Assessment failed after {context_time:.1f}s")

    # ── Step 4: Full pipeline test ───────────────────────────────────────
    print(f"\n  [4/4] Full pipeline (analyze_signal_charts)...")
    t0 = time.time()
    full_result = analyze_signal_charts(mt5, mock_signal, mock_breakdown)
    total_time = time.time() - t0

    print(f"\n  {'─' * 60}")
    print(f"  FULL PIPELINE RESULT:")
    print(f"  {'─' * 60}")
    print(f"  Alignment:     {full_result['alignment']}")
    print(f"  Risk Factor:   {full_result['risk_factor']:.2f}")
    print(f"  Red Flags:     {len(full_result['red_flags'])}")
    print(f"  Supports:      {len(full_result['supports'])}")
    print(f"  Total Time:    {full_result['elapsed_seconds']:.1f}s")
    print(f"  Chart Report:  {'Yes' if full_result['chart_report'] else 'No'}")
    print(f"  Assessment:    {'Yes' if full_result['risk_assessment'] else 'No'}")

    # Show effective risk calculation
    base_risk = cfg.MAX_RISK_PER_TRADE_PCT
    tier_factor = cfg.RISK_FACTOR_DEEP_REVIEW  # structural override for score ~62
    chart_factor = full_result["risk_factor"]
    effective = base_risk * tier_factor * chart_factor
    print(f"\n  RISK SIZING:")
    print(f"    Base risk:     {base_risk:.1f}%")
    print(f"    Tier factor:   {tier_factor:.2f} (structural override)")
    print(f"    Chart factor:  {chart_factor:.2f}")
    print(f"    Effective:     {effective:.3f}% of equity")
    print(f"    On $1000:      ${1000 * effective / 100:.2f} at risk")

print(f"\n{'=' * 70}")
print(f"  TEST COMPLETE")
print(f"{'=' * 70}")
print(f"\nChart images saved to: {chart_dir}")
print("Open them to see what GPT-5.2 analyzed.\n")

mt5.disconnect()
