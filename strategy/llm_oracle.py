import json
import os
import pandas as pd
from typing import Optional
from strategy.types import Phase

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

class LLMOracle:
    def __init__(self, model: str = "gpt-5.2"):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            print("[WARNING] OPENAI_API_KEY not set. LLM Oracle will be disabled.")
        self.client = OpenAI(api_key=self.api_key) if OpenAI and self.api_key else None
        self.model = model

    def evaluate_regime(
        self,
        symbol: str,
        window: pd.DataFrame,
        current_phase: Phase,
        open_positions: int,
        unrealized_pnl: float,
        realized_chop: float,
        realized_trend: float,
        atr_pips: float,
        kaufman_er: float,
        linreg_r2: float,
        donchian_high: float,
        donchian_low: float,
        is_squeeze: bool,
    ) -> Optional[Phase]:
        if not self.client:
            return None

        # Get last 10 bars for micro-structure analysis
        recent = window.tail(10).copy()
        recent['time'] = recent['time'].dt.strftime('%H:%M')
        bars_str = recent[['time', 'open', 'high', 'low', 'close', 'tick_volume']].to_string(index=False)

        prompt = f"""
        You are a quantitative regime classifier for a delta-neutral grid trading bot.
        Analyze the following market data for {symbol} and determine the true market regime.
        Your primary goal is to PREVENT FALSE POSITIVES (fakeouts/liquidity sweeps) and PREVENT FALSE NEGATIVES (missing real trends).

        Current State:
        - Current Phase: {current_phase.name}
        - Open Positions: {open_positions}
        - Unrealized PnL: {unrealized_pnl:.2f}
        - Realized Chop (Free Capital): {realized_chop:.2f}
        - Realized Trend (Hostage Capital): {realized_trend:.2f}
        
        Quantitative Indicators (Calculated over last 20 bars):
        - Current ATR (pips): {atr_pips:.2f}
        - Kaufman Efficiency Ratio (ER): {kaufman_er:.2f} (1.0 = clean trend, 0.0 = pure chop)
        - Linear Regression R^2: {linreg_r2:.2f} (High = persistent trend, Low = noise)
        - Donchian Channel (20): High={donchian_high:.5f}, Low={donchian_low:.5f}
        - Volatility Squeeze Active: {is_squeeze} (True = compression, expect expansion)

        Recent OHLCV (Last 10 bars):
        {bars_str}

        Rules for Classification:
        1. RANGE: Price is oscillating. ER is low (< 0.5), R^2 is low. If you see a sudden spike but it has a long wick and immediately reverses (liquidity sweep), it is a fakeout. Stay in RANGE.
        2. TREND_LOCK: Price is breaking out with sustained momentum. Look for ER > 0.6, R^2 > 0.6, and price breaking the Donchian High/Low. If Squeeze was active and now breaking out, high confidence.
        3. EXHAUSTION_CONFIRM: A previous trend has stopped making new highs/lows and is moving sideways with decreasing volume. ER is dropping.
        4. GARBAGE_COLLECT: The trend is fully dead, and it is safe to close underwater bags using the Realized Trend capital.
        5. RISK_OFF: Market is completely erratic, spreads are likely blown out, or drawdown is critical.

        Respond ONLY in valid JSON format:
        {{"phase": "PHASE_NAME", "confidence": 0.95, "reasoning": "short explanation of why it's a fakeout or real trend"}}
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
                timeout=15
            )
            result = json.loads(response.choices[0].message.content)
            phase_str = result.get("phase")
            
            # Optional: Print the LLM's reasoning for debugging
            # print(f"[LLM Oracle] {symbol} -> {phase_str} (Conf: {result.get('confidence')}): {result.get('reasoning')}")
            
            if phase_str in Phase.__members__:
                return Phase[phase_str]
        except Exception as e:
            print(f"[LLM Oracle Error] {e}")
        return None
