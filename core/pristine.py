"""
===============================================================================
  Pristine Method — Price-Action Analysis Engine
===============================================================================
  Implements Greg Capra's Pristine trading methodology:
    Ch. 1  — Four Stages of the Market (stage classification)
    Ch. 2  — Candlestick Analysis (WRB/NRB/COG/Tail bar classification)
    Ch. 3  — Objective Support & Resistance (pivot-based S/R)
    Ch. 4  — Moving Averages as visual aids (20/40/200)
    Ch. 5  — Volume Classification (professional vs novice)
    Ch. 6  — Retracement Analysis (pullback quality)
    Ch. 7  — Bar-by-Bar Analysis (real-time trade health)
    Ch. 10 — The Trend (pivot-based trend definition)
    Ch. 12 — Multiple Time Frames (sweet spot / sour spot)
    Ch. 13 — Making Failure Work (BBF, failure patterns)

  Design principle:  Price is the ONLY truth.  Indicators are visual aids.
  Every function in this module works from raw OHLCV data and pivot structure.
===============================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as cfg
from utils.logger import get_logger

log = get_logger("pristine")


# ═════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════
# Candle classification thresholds (Ch. 2)
WRB_BODY_RATIO: float = 2.0     # body > 2x avg = Wide Range Body
NRB_BODY_RATIO: float = 0.5     # body < 0.5x avg = Narrow Range Body
COG_THRESHOLD: float = 0.25     # close in top/bottom 25% of range
TAIL_RATIO: float = 2.0         # tail > 2x body = significant tail

# Retracement thresholds (Ch. 6)
RET_PRISTINE: float = 0.40      # < 40% = pristine quality pullback
RET_HEALTHY: float = 0.50       # 40-50% = healthy
RET_DEEP: float = 0.60          # 50-60% = deep
RET_MAX_GATE: float = 0.80      # > 80% = trend broken, hard reject

# Volume thresholds (Ch. 5)
VOL_SPIKE: float = 1.8          # > 1.8x avg = significant
VOL_DECLINING: float = 0.7      # pullback vol < 0.7x impulse = healthy

# Stage MA convergence (Ch. 1)
STAGE_CONVERGENCE_PCT: float = 1.5  # MAs within 1.5% = converging

# Pivot lookback (Ch. 10)
PIVOT_LOOKBACK: int = 5         # bars each side for swing detection


# ═════════════════════════════════════════════════════════════════════════════
#  1. CANDLE CLASSIFICATION  (Chapter 2)
# ═════════════════════════════════════════════════════════════════════════════

def classify_candle(df: pd.DataFrame, idx: int = -1) -> dict:
    """
    Classify a single bar using the Pristine methodology (Ch. 2).

    Instead of memorising pattern names, we ask:
      "What does this bar tell us about supply and demand?"

    Categories:
      WRB — Wide Range Body: strong conviction, one side dominated
      NRB — Narrow Range Body: indecision, neither side winning
      COG — Closing On Gap/extreme: close in top/bottom 25% of range
      Tail — Long upper/lower tail: one side attempted but was rejected

    Returns dict with keys:
      type        : "WRB" | "NRB" | "normal"
      cog         : "bullish" | "bearish" | None
      tail        : "demand_rejection" | "supply_rejection" | None
      bias        : +1 (bullish) | -1 (bearish) | 0 (neutral)
      body_ratio  : body / avg_body (how big relative to recent)
      range_ratio : range / avg_range
      is_bullish  : bool (close > open)
    """
    if df is None or len(df) < 12 or abs(idx) > len(df):
        return _empty_candle_class()

    # Resolve negative index
    actual_idx = idx if idx >= 0 else len(df) + idx
    if actual_idx < 10 or actual_idx >= len(df):
        return _empty_candle_class()

    row = df.iloc[actual_idx]
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    bar_range = h - l
    body = abs(c - o)
    is_bull = c > o

    if bar_range == 0:
        return _empty_candle_class()

    # Average body and range over the prior 10 bars (not including current)
    lookback_slice = df.iloc[max(0, actual_idx - 10):actual_idx]
    avg_body = (lookback_slice["close"] - lookback_slice["open"]).abs().mean()
    avg_range = (lookback_slice["high"] - lookback_slice["low"]).mean()

    if avg_body == 0:
        avg_body = body if body > 0 else 1e-10
    if avg_range == 0:
        avg_range = bar_range if bar_range > 0 else 1e-10

    body_ratio = body / avg_body
    range_ratio = bar_range / avg_range

    # ── Type classification ──────────────────────────────────────────────
    if body_ratio >= WRB_BODY_RATIO:
        bar_type = "WRB"
    elif body_ratio <= NRB_BODY_RATIO:
        bar_type = "NRB"
    else:
        bar_type = "normal"

    # ── Closing On Gap (COG) ─────────────────────────────────────────────
    close_position = (c - l) / bar_range  # 0 = closed at low, 1 = closed at high
    cog = None
    if close_position >= (1 - COG_THRESHOLD):
        cog = "bullish"
    elif close_position <= COG_THRESHOLD:
        cog = "bearish"

    # ── Tail analysis ────────────────────────────────────────────────────
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    tail = None
    if body > 0:
        if upper_wick >= TAIL_RATIO * body and lower_wick < body * 0.3:
            tail = "supply_rejection"   # sellers tried, failed → bearish tail
        elif lower_wick >= TAIL_RATIO * body and upper_wick < body * 0.3:
            tail = "demand_rejection"   # buyers stepped in → bullish tail (hammer)

    # ── Composite bias ───────────────────────────────────────────────────
    bias = 0
    if bar_type == "WRB":
        bias = 1 if is_bull else -1
    elif cog == "bullish":
        bias += 1
    elif cog == "bearish":
        bias -= 1
    if tail == "demand_rejection":
        bias += 1
    elif tail == "supply_rejection":
        bias -= 1
    bias = max(-1, min(1, bias))  # clamp

    return {
        "type": bar_type,
        "cog": cog,
        "tail": tail,
        "bias": bias,
        "body_ratio": round(body_ratio, 2),
        "range_ratio": round(range_ratio, 2),
        "is_bullish": is_bull,
    }


def classify_last_n_candles(df: pd.DataFrame, n: int = 5) -> list[dict]:
    """
    Classify the last *n* candles for bar-by-bar analysis (Ch. 7).
    Returns list ordered oldest → newest.
    """
    if df is None or len(df) < n + 10:
        return []
    results = []
    for offset in range(n, 0, -1):
        results.append(classify_candle(df, idx=-offset))
    return results


def _empty_candle_class() -> dict:
    return {
        "type": "normal", "cog": None, "tail": None,
        "bias": 0, "body_ratio": 1.0, "range_ratio": 1.0, "is_bullish": True,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  2. PIVOT DETECTION & CLASSIFICATION  (Chapter 10)
# ═════════════════════════════════════════════════════════════════════════════

def find_pivots(df: pd.DataFrame, lookback: int | None = None) -> list[dict]:
    """
    Identify pivot highs and pivot lows from OHLC data (Ch. 10).

    A pivot high:  bar whose high is the highest of *lookback* bars each side.
    A pivot low:   bar whose low is the lowest of *lookback* bars each side.

    Each pivot is returned as:
        {"type": "high"|"low", "price": float, "time": datetime,
         "idx": int, "major": False}

    Major/minor classification is done in a second pass by
    classify_pivots_major_minor().
    """
    lookback = lookback or PIVOT_LOOKBACK
    if df is None or len(df) < lookback * 3:
        return []

    highs_v = df["high"].values
    lows_v = df["low"].values
    pivots = []

    for i in range(lookback, len(df) - lookback):
        window_h = highs_v[i - lookback: i + lookback + 1]
        window_l = lows_v[i - lookback: i + lookback + 1]

        if highs_v[i] == window_h.max():
            pivots.append({
                "type": "high",
                "price": float(highs_v[i]),
                "time": df.index[i],
                "idx": i,
                "major": False,
            })

        if lows_v[i] == window_l.min():
            pivots.append({
                "type": "low",
                "price": float(lows_v[i]),
                "time": df.index[i],
                "idx": i,
                "major": False,
            })

    # Sort chronologically
    pivots.sort(key=lambda p: p["idx"])
    return pivots


def classify_pivots_major_minor(pivots: list[dict]) -> list[dict]:
    """
    Walk the pivot sequence and classify each as Major or Minor (Ch. 10).

    A pivot high is Major if it exceeds the previous 2 pivot highs.
    A pivot low is Major if it undercuts the previous 2 pivot lows.
    Everything else is Minor (a pullback within the larger trend).

    The book says: "Major pivots change the direction of the higher
    timeframe.  Minor pivots are noise within the trend."
    """
    if not pivots:
        return pivots

    prev_highs: list[float] = []
    prev_lows: list[float] = []

    for pv in pivots:
        if pv["type"] == "high":
            # Major if higher than last 2 pivot highs
            if len(prev_highs) >= 2 and pv["price"] > max(prev_highs[-2:]):
                pv["major"] = True
            elif len(prev_highs) == 0:
                pv["major"] = True  # first pivot = major by default
            prev_highs.append(pv["price"])
        else:  # "low"
            # Major if lower than last 2 pivot lows
            if len(prev_lows) >= 2 and pv["price"] < min(prev_lows[-2:]):
                pv["major"] = True
            elif len(prev_lows) == 0:
                pv["major"] = True
            prev_lows.append(pv["price"])

    return pivots


def determine_trend_from_pivots(pivots: list[dict]) -> dict:
    """
    The Pristine trend definition (Ch. 10):

    Uptrend:   Higher Pivot Highs (HPH) + Higher Pivot Lows (HPL)
    Downtrend: Lower Pivot Highs (LPH) + Lower Pivot Lows (LPL)
    Range:     No consistent pattern

    Returns:
        trend       : "uptrend" | "downtrend" | "range"
        strength    : "strong" | "moderate" | "weak"
        last_ph     : most recent pivot high price
        last_pl     : most recent pivot low price
        hph_count   : consecutive higher pivot highs
        hpl_count   : consecutive higher pivot lows
        lph_count   : consecutive lower pivot highs
        lpl_count   : consecutive lower pivot lows
    """
    result = {
        "trend": "range", "strength": "weak",
        "last_ph": 0.0, "last_pl": 0.0,
        "hph_count": 0, "hpl_count": 0,
        "lph_count": 0, "lpl_count": 0,
    }

    # Separate recent pivot highs and lows
    p_highs = [p for p in pivots if p["type"] == "high"]
    p_lows = [p for p in pivots if p["type"] == "low"]

    if len(p_highs) < 2 or len(p_lows) < 2:
        return result

    result["last_ph"] = p_highs[-1]["price"]
    result["last_pl"] = p_lows[-1]["price"]

    # Count consecutive higher/lower pivots (from the most recent backward)
    hph = 0
    for i in range(len(p_highs) - 1, 0, -1):
        if p_highs[i]["price"] > p_highs[i - 1]["price"]:
            hph += 1
        else:
            break
    result["hph_count"] = hph

    hpl = 0
    for i in range(len(p_lows) - 1, 0, -1):
        if p_lows[i]["price"] > p_lows[i - 1]["price"]:
            hpl += 1
        else:
            break
    result["hpl_count"] = hpl

    lph = 0
    for i in range(len(p_highs) - 1, 0, -1):
        if p_highs[i]["price"] < p_highs[i - 1]["price"]:
            lph += 1
        else:
            break
    result["lph_count"] = lph

    lpl = 0
    for i in range(len(p_lows) - 1, 0, -1):
        if p_lows[i]["price"] < p_lows[i - 1]["price"]:
            lpl += 1
        else:
            break
    result["lpl_count"] = lpl

    # ── Trend determination ──────────────────────────────────────────────
    if hph >= 2 and hpl >= 2:
        result["trend"] = "uptrend"
        result["strength"] = "strong" if (hph >= 3 and hpl >= 3) else "moderate"
    elif lph >= 2 and lpl >= 2:
        result["trend"] = "downtrend"
        result["strength"] = "strong" if (lph >= 3 and lpl >= 3) else "moderate"
    elif hph >= 1 and hpl >= 1:
        result["trend"] = "uptrend"
        result["strength"] = "weak"
    elif lph >= 1 and lpl >= 1:
        result["trend"] = "downtrend"
        result["strength"] = "weak"
    else:
        result["trend"] = "range"
        result["strength"] = "weak"

    return result


# ═════════════════════════════════════════════════════════════════════════════
#  3. STAGE CLASSIFICATION  (Chapter 1)
# ═════════════════════════════════════════════════════════════════════════════

def classify_stage(df: pd.DataFrame, pivot_trend: dict | None = None) -> dict:
    """
    Determine the current market stage (Ch. 1).

    Stage 1 — Ambivalence (Accumulation):
        After a decline, price goes sideways.  MAs flatten and converge.
        Volume is low, no conviction.  DO NOT TRADE.

    Stage 2 — Uptrend (Markup):
        Price breaks out of Stage 1.  MAs fan out upward.
        Price > 20 EMA > 40 EMA.  ONLY BUY.

    Stage 3 — Uncertainty (Distribution):
        After an uptrend, price goes sideways.  MAs flatten.
        Volume picks up on declines.  DO NOT TRADE.

    Stage 4 — Downtrend (Markdown):
        Price breaks down from Stage 3.  MAs fan out downward.
        Price < 20 EMA < 40 EMA.  ONLY SELL.

    Returns:
        stage            : 1 | 2 | 3 | 4
        confidence       : 0.0 - 1.0
        tradeable        : bool
        allowed_direction: "BUY" | "SELL" | None
        description      : str
    """
    result = {
        "stage": 1, "confidence": 0.0, "tradeable": False,
        "allowed_direction": None, "description": "Insufficient data",
    }

    if df is None or len(df) < 50:
        return result

    close = df["close"].values
    current_price = close[-1]

    # ── Compute MAs if not already present ───────────────────────────────
    ma20 = _safe_ema(df, 20)
    ma40 = _safe_ema(df, 40)
    ma200 = _safe_sma(df, 200) if len(df) >= 200 else None

    if ma20 is None or ma40 is None:
        return result

    ma20_val = ma20[-1]
    ma40_val = ma40[-1]
    ma200_val = ma200[-1] if ma200 is not None else None

    # ── MA slope (direction over last 10 bars) ───────────────────────────
    ma20_slope = (ma20[-1] - ma20[-11]) / ma20[-11] * 100 if len(ma20) > 11 and ma20[-11] != 0 else 0
    ma40_slope = (ma40[-1] - ma40[-11]) / ma40[-11] * 100 if len(ma40) > 11 and ma40[-11] != 0 else 0

    # ── MA convergence / divergence ──────────────────────────────────────
    ma_spread_pct = abs(ma20_val - ma40_val) / ma40_val * 100 if ma40_val != 0 else 0
    mas_converging = ma_spread_pct < STAGE_CONVERGENCE_PCT

    # ── Price position relative to MAs ───────────────────────────────────
    price_above_20 = current_price > ma20_val
    price_above_40 = current_price > ma40_val
    ma20_above_40 = ma20_val > ma40_val

    # ── Get pivot trend if not supplied ──────────────────────────────────
    if pivot_trend is None:
        pvs = find_pivots(df)
        pvs = classify_pivots_major_minor(pvs)
        pivot_trend = determine_trend_from_pivots(pvs)

    pv_trend = pivot_trend.get("trend", "range")

    # ── Stage determination logic ────────────────────────────────────────

    # Stage 2: Uptrend
    if (price_above_20 and price_above_40 and ma20_above_40
            and ma20_slope > 0.1 and ma40_slope >= 0):
        conf = 0.5
        if pv_trend == "uptrend":
            conf += 0.3
        if pivot_trend.get("strength") == "strong":
            conf += 0.1
        if ma200_val is not None and current_price > ma200_val:
            conf += 0.1
        result = {
            "stage": 2, "confidence": min(conf, 1.0), "tradeable": True,
            "allowed_direction": "BUY",
            "description": "Stage 2 Uptrend — MAs fanning up, HPH+HPL",
        }
        return result

    # Stage 4: Downtrend
    if (not price_above_20 and not price_above_40 and not ma20_above_40
            and ma20_slope < -0.1 and ma40_slope <= 0):
        conf = 0.5
        if pv_trend == "downtrend":
            conf += 0.3
        if pivot_trend.get("strength") == "strong":
            conf += 0.1
        if ma200_val is not None and current_price < ma200_val:
            conf += 0.1
        result = {
            "stage": 4, "confidence": min(conf, 1.0), "tradeable": True,
            "allowed_direction": "SELL",
            "description": "Stage 4 Downtrend — MAs fanning down, LPH+LPL",
        }
        return result

    # Stage 1 or 3: Sideways
    # Distinguish by what came before (Ch. 1):
    #   After a decline (recent Stage 4 / downtrend) → Stage 1 (accumulation)
    #   After an advance (recent Stage 2 / uptrend) → Stage 3 (distribution)
    if mas_converging:
        # Look at MA slopes 30 bars ago to guess what came before
        prior_slope = 0
        if len(ma20) > 40:
            prior_slope = (ma20[-30] - ma20[-40]) / ma20[-40] * 100 if ma20[-40] != 0 else 0

        if prior_slope < -0.1 or (ma200_val is not None and current_price < ma200_val):
            result = {
                "stage": 1, "confidence": 0.6, "tradeable": False,
                "allowed_direction": None,
                "description": "Stage 1 Accumulation — sideways after decline",
            }
        else:
            result = {
                "stage": 3, "confidence": 0.6, "tradeable": False,
                "allowed_direction": None,
                "description": "Stage 3 Distribution — sideways after advance",
            }
        return result

    # Ambiguous — default to whichever is closest
    if ma20_slope > 0:
        result = {
            "stage": 2, "confidence": 0.35, "tradeable": True,
            "allowed_direction": "BUY",
            "description": "Weak Stage 2 — some upward tendency",
        }
    elif ma20_slope < 0:
        result = {
            "stage": 4, "confidence": 0.35, "tradeable": True,
            "allowed_direction": "SELL",
            "description": "Weak Stage 4 — some downward tendency",
        }
    else:
        result = {
            "stage": 1, "confidence": 0.3, "tradeable": False,
            "allowed_direction": None,
            "description": "Ambiguous — MAs flat, no clear stage",
        }

    return result


# ═════════════════════════════════════════════════════════════════════════════
#  4. RETRACEMENT ANALYSIS  (Chapter 6)
# ═════════════════════════════════════════════════════════════════════════════

def analyze_retracement(
    df: pd.DataFrame,
    pivots: list[dict],
    direction: int,
) -> dict:
    """
    Measure pullback quality (Ch. 6).

    The depth of a retracement reveals trend health:
      < 40%  (pristine):  Very strong trend, shallow pullback — best entries
      40-50% (healthy):   Normal, standard pullback
      50-60% (deep):      Trend weakening but potentially still valid
      60-80% (failing):   Trend in serious trouble
      > 80%  (broken):    Trend is dead — HARD REJECT

    Also checks proximity to key MAs:
      near_ma20: Pullback to 20 EMA area (Ch. 6 "textbook setup")
      near_ma40: Pullback to 40 EMA area (deeper but acceptable)
    """
    result = {
        "retracement_pct": 0.0,
        "quality": "unknown",
        "impulse_start": 0.0,
        "impulse_end": 0.0,
        "current_price": 0.0,
        "near_ma20": False,
        "near_ma40": False,
    }

    if not pivots or df is None or len(df) < 20:
        return result

    current_price = df["close"].iloc[-1]
    result["current_price"] = current_price

    p_highs = [p for p in pivots if p["type"] == "high"]
    p_lows = [p for p in pivots if p["type"] == "low"]

    if direction == 1 and len(p_highs) >= 1 and len(p_lows) >= 1:
        # For an uptrend pullback: impulse = last pivot low → last pivot high
        # Find the most recent pivot high THEN the pivot low before it
        last_ph = p_highs[-1]
        prior_lows = [p for p in p_lows if p["idx"] < last_ph["idx"]]
        if not prior_lows:
            return result
        last_pl = prior_lows[-1]

        impulse_start = last_pl["price"]
        impulse_end = last_ph["price"]

    elif direction == -1 and len(p_highs) >= 1 and len(p_lows) >= 1:
        # For a downtrend pullback: impulse = last pivot high → last pivot low
        last_pl = p_lows[-1]
        prior_highs = [p for p in p_highs if p["idx"] < last_pl["idx"]]
        if not prior_highs:
            return result
        last_ph = prior_highs[-1]

        impulse_start = last_ph["price"]
        impulse_end = last_pl["price"]
    else:
        return result

    impulse_range = abs(impulse_end - impulse_start)
    if impulse_range == 0:
        return result

    result["impulse_start"] = impulse_start
    result["impulse_end"] = impulse_end

    # How far has price pulled back from the impulse end?
    if direction == 1:
        pullback = impulse_end - current_price  # positive means price pulled back
    else:
        pullback = current_price - impulse_end   # positive means price pulled back up

    ret_pct = max(0, pullback / impulse_range)
    result["retracement_pct"] = round(ret_pct, 3)

    # Quality label
    if ret_pct < 0:
        result["quality"] = "none"  # price hasn't pulled back (still extending)
    elif ret_pct < RET_PRISTINE:
        result["quality"] = "pristine"
    elif ret_pct < RET_HEALTHY:
        result["quality"] = "healthy"
    elif ret_pct < RET_DEEP:
        result["quality"] = "deep"
    elif ret_pct < RET_MAX_GATE:
        result["quality"] = "failing"
    else:
        result["quality"] = "broken"

    # ── MA proximity check (Ch. 6 — "pullback to the 20 EMA area") ──────
    ma20 = _safe_ema(df, 20)
    ma40 = _safe_ema(df, 40)

    if ma20 is not None and len(df) > 0:
        atr_est = _estimate_atr(df)
        if atr_est > 0:
            dist_20 = abs(current_price - ma20[-1])
            dist_40 = abs(current_price - ma40[-1]) if ma40 is not None else float("inf")
            result["near_ma20"] = dist_20 <= atr_est * 1.0
            result["near_ma40"] = dist_40 <= atr_est * 1.0

    return result


# ═════════════════════════════════════════════════════════════════════════════
#  5. VOLUME CLASSIFICATION  (Chapter 5)
# ═════════════════════════════════════════════════════════════════════════════

def classify_volume(
    df: pd.DataFrame,
    pivots: list[dict],
    direction: int,
) -> dict:
    """
    Classify volume behaviour (Ch. 5).

    Professional Volume: high volume that IGNITES a new move (at the start
        of an impulse leg, breaking out of consolidation).
    Novice Volume: high volume that ENDS a move (climactic buying/selling
        at tops/bottoms after a multi-bar run).

    Also measures pullback volume vs impulse volume:
        Declining vol on pullback = healthy (corrective, not reversal)
        Rising vol on pullback = dangerous (real pressure against trend)
    """
    result = {
        "current_vol_type": "normal",
        "pullback_vol_trend": "flat",
        "impulse_vol_trend": "flat",
        "vol_confirms_trend": False,
    }

    # FIXED: MT5Connector.get_rates() renames tick_volume → volume.
    # Check for "volume" directly instead of the dead "tick_volume" branch.
    if df is None or len(df) < 30 or "volume" not in df.columns:
        return result
    vol_col = "volume"

    vol = df[vol_col].values
    close = df["close"].values

    # Current volume vs average
    avg_vol_20 = np.mean(vol[-21:-1]) if len(vol) > 21 else np.mean(vol[:-1])
    current_vol = vol[-1]

    if avg_vol_20 == 0:
        return result

    vol_ratio = current_vol / avg_vol_20

    # ── Professional vs Novice ───────────────────────────────────────────
    # Professional: spike at the start of a move (within 2 bars of a pivot)
    # Novice: spike at the end of a multi-bar run (5+ bars in same direction)
    if vol_ratio >= VOL_SPIKE:
        # Count consecutive bars in the same direction before this bar
        consec = 0
        for i in range(len(close) - 2, max(len(close) - 10, 0), -1):
            if direction == 1 and close[i] > close[i - 1]:
                consec += 1
            elif direction == -1 and close[i] < close[i - 1]:
                consec += 1
            else:
                break

        if consec >= 4:
            result["current_vol_type"] = "novice"  # climactic, late money
        else:
            result["current_vol_type"] = "professional"  # fresh move

    # ── Pullback volume analysis ─────────────────────────────────────────
    # Find the most recent pullback phase (bars moving against trend)
    pullback_vols = []
    impulse_vols = []

    for i in range(len(close) - 1, max(len(close) - 20, 1), -1):
        bar_direction = 1 if close[i] > close[i - 1] else -1
        if bar_direction != direction:
            pullback_vols.append(vol[i])
        else:
            impulse_vols.append(vol[i])

    if pullback_vols and impulse_vols:
        avg_pb_vol = np.mean(pullback_vols)
        avg_imp_vol = np.mean(impulse_vols)

        if avg_imp_vol > 0:
            pb_ratio = avg_pb_vol / avg_imp_vol
            if pb_ratio < VOL_DECLINING:
                result["pullback_vol_trend"] = "declining"
            elif pb_ratio > 1.3:
                result["pullback_vol_trend"] = "rising"
            else:
                result["pullback_vol_trend"] = "flat"

    # ── Impulse volume trend ─────────────────────────────────────────────
    if impulse_vols and len(impulse_vols) >= 3:
        first_half = np.mean(impulse_vols[len(impulse_vols)//2:])
        second_half = np.mean(impulse_vols[:len(impulse_vols)//2])
        if second_half > first_half * 1.1:
            result["impulse_vol_trend"] = "expanding"
        elif second_half < first_half * 0.8:
            result["impulse_vol_trend"] = "contracting"

    # ── Overall confirmation ─────────────────────────────────────────────
    result["vol_confirms_trend"] = (
        result["pullback_vol_trend"] == "declining"
        and result["current_vol_type"] != "novice"
    )

    return result


# ═════════════════════════════════════════════════════════════════════════════
#  6. BAR-BY-BAR ANALYSIS  (Chapter 7)
# ═════════════════════════════════════════════════════════════════════════════

def bar_by_bar_assessment(
    df: pd.DataFrame,
    direction: int,
    entry_idx: int | None = None,
) -> dict:
    """
    Real-time bar-by-bar trade health assessment (Ch. 7).

    After entry, each new bar is evaluated:
      RBI (Red Bar Ignored): A red bar that doesn't follow through —
          the next bar closes above the red bar's high.  Bullish.
      GBI (Green Bar Ignored): A green bar that doesn't follow through.
          Bearish.
      Narrowing ranges: Compression → continuation likely.
      Widening ranges against: Increasing counter-pressure → exit.
      Close position: Where bars close relative to their range.

    Returns:
        health       : "strong" | "ok" | "warning" | "exit"
        rbi_count    : red bars ignored (bullish context)
        gbi_count    : green bars ignored (bearish context)
        bars_against : consecutive bars against the trade direction
        range_trend  : "narrowing" | "widening" | "stable"
        reasons      : list of explanations
    """
    result = {
        "health": "ok", "rbi_count": 0, "gbi_count": 0,
        "bars_against": 0, "range_trend": "stable", "reasons": [],
    }

    if df is None or len(df) < 10:
        return result

    # Default: analyze last 10 bars (or from entry_idx)
    start = entry_idx if entry_idx is not None else max(0, len(df) - 10)
    if start >= len(df) - 1:
        return result

    analysis_df = df.iloc[start:]
    if len(analysis_df) < 2:
        return result

    closes = analysis_df["close"].values
    opens = analysis_df["open"].values
    highs = analysis_df["high"].values
    lows = analysis_df["low"].values

    reasons = []

    # ── Count RBI / GBI ──────────────────────────────────────────────────
    rbi = 0
    gbi = 0
    for i in range(1, len(closes) - 1):
        bar_bullish = closes[i] > opens[i]
        next_bar_bullish = closes[i + 1] > opens[i + 1]

        if not bar_bullish and next_bar_bullish:
            # Red bar followed by green that closes above red's high
            if closes[i + 1] > highs[i]:
                rbi += 1
        elif bar_bullish and not next_bar_bullish:
            # Green bar followed by red that closes below green's low
            if closes[i + 1] < lows[i]:
                gbi += 1

    result["rbi_count"] = rbi
    result["gbi_count"] = gbi

    if direction == 1 and rbi >= 2:
        reasons.append(f"{rbi} Red Bars Ignored — sellers failing")
    if direction == -1 and gbi >= 2:
        reasons.append(f"{gbi} Green Bars Ignored — buyers failing")

    # ── Consecutive bars against ─────────────────────────────────────────
    bars_against = 0
    for i in range(len(closes) - 1, 0, -1):
        bar_dir = 1 if closes[i] > opens[i] else -1
        if bar_dir == -direction:
            bars_against += 1
        else:
            break

    result["bars_against"] = bars_against
    if bars_against >= 3:
        reasons.append(f"{bars_against} consecutive bars against position")

    # ── Range trend (narrowing/widening) ─────────────────────────────────
    if len(analysis_df) >= 5:
        ranges = highs - lows
        recent_3 = np.mean(ranges[-3:])
        older_3 = np.mean(ranges[-6:-3]) if len(ranges) >= 6 else np.mean(ranges[:-3])

        if older_3 > 0:
            ratio = recent_3 / older_3
            if ratio < 0.6:
                result["range_trend"] = "narrowing"
                reasons.append("Bar ranges narrowing — compression")
            elif ratio > 1.5:
                result["range_trend"] = "widening"
                reasons.append("Bar ranges widening — increasing volatility")

    # ── Check WRB against position ───────────────────────────────────────
    last_candle = classify_candle(df, idx=-1)
    if last_candle["type"] == "WRB" and last_candle["bias"] == -direction:
        reasons.append("WRB against position — strong counter-pressure")

    # ── Determine overall health ─────────────────────────────────────────
    danger_score = 0
    if bars_against >= 3:
        danger_score += 2
    if bars_against >= 5:
        danger_score += 3
    if last_candle["type"] == "WRB" and last_candle["bias"] == -direction:
        danger_score += 2
    if result["range_trend"] == "widening" and bars_against >= 2:
        danger_score += 1

    # Positive signals
    if direction == 1 and rbi >= 2:
        danger_score -= 1
    if direction == -1 and gbi >= 2:
        danger_score -= 1
    if result["range_trend"] == "narrowing":
        danger_score -= 1

    if danger_score >= 5:
        result["health"] = "exit"
    elif danger_score >= 3:
        result["health"] = "warning"
    elif danger_score <= -1:
        result["health"] = "strong"
    else:
        result["health"] = "ok"

    result["reasons"] = reasons
    return result


# ═════════════════════════════════════════════════════════════════════════════
#  7. SWEET SPOT / SOUR SPOT  (Chapter 12)
# ═════════════════════════════════════════════════════════════════════════════

def detect_sweet_sour_spot(
    tf_analyses: dict,
    direction: int,
    entry_tf: str = "M15",
    trend_tf: str = "H1",
    higher_tf: str = "D1",
) -> dict:
    """
    Multi-timeframe alignment quality (Ch. 12).

    Sweet Spot: "Everything aligns — the micro TF is giving a buy signal
      right at a macro TF support, and the macro is in a clean Stage 2."

    Sour Spot: "You're buying into the ceiling — the micro looks bullish
      but the macro has major resistance overhead."

    tf_analyses: dict mapping TF name → {
        "stage": dict (from classify_stage),
        "pivot_trend": dict (from determine_trend_from_pivots),
        "retracement": dict (from analyze_retracement),
        "sr_levels": list (S/R levels),
        "candle_class": list (from classify_last_n_candles),
        "current_price": float,
        "atr": float,
    }

    Returns:
        type             : "sweet_spot" | "sour_spot" | "neutral"
        score            : -1.0 to +1.0
        reasons          : list[str]
        macro_stage      : int (higher TF stage)
        micro_retracement: str (entry TF retracement quality)
    """
    result = {
        "type": "neutral", "score": 0.0, "reasons": [],
        "macro_stage": 0, "micro_retracement": "unknown",
    }

    htf = tf_analyses.get(higher_tf, {})
    ttf = tf_analyses.get(trend_tf, {})
    etf = tf_analyses.get(entry_tf, {})

    if not htf or not ttf:
        return result

    reasons = []
    score = 0.0

    # ── Higher TF stage check ────────────────────────────────────────────
    htf_stage = htf.get("stage", {})
    macro_stage = htf_stage.get("stage", 0)
    result["macro_stage"] = macro_stage

    stage_ok = False
    if direction == 1 and macro_stage == 2:
        stage_ok = True
        score += 0.3
        reasons.append(f"{higher_tf} in Stage 2 (uptrend) — aligned with BUY")
    elif direction == -1 and macro_stage == 4:
        stage_ok = True
        score += 0.3
        reasons.append(f"{higher_tf} in Stage 4 (downtrend) — aligned with SELL")
    elif macro_stage in (1, 3):
        score -= 0.4
        reasons.append(f"{higher_tf} in Stage {macro_stage} (no trend) — SOUR SPOT")
    else:
        score -= 0.2
        reasons.append(f"{higher_tf} Stage {macro_stage} against trade direction")

    # ── Trading TF pivot trend alignment ─────────────────────────────────
    ttf_pv = ttf.get("pivot_trend", {})
    ttf_trend = ttf_pv.get("trend", "range")

    if (direction == 1 and ttf_trend == "uptrend") or \
       (direction == -1 and ttf_trend == "downtrend"):
        score += 0.2
        reasons.append(f"{trend_tf} pivot trend = {ttf_trend} — aligned")
    elif ttf_trend == "range":
        score -= 0.1
        reasons.append(f"{trend_tf} pivot trend = range — neutral")
    else:
        score -= 0.3
        reasons.append(f"{trend_tf} pivot trend = {ttf_trend} — conflicts with direction")

    # ── Entry TF retracement quality ─────────────────────────────────────
    etf_ret = etf.get("retracement", {})
    ret_quality = etf_ret.get("quality", "unknown")
    result["micro_retracement"] = ret_quality

    if ret_quality in ("pristine", "healthy"):
        score += 0.2
        reasons.append(f"{entry_tf} retracement = {ret_quality} — good pullback")
    elif ret_quality == "none":
        score += 0.1  # extending, not pulling back
        reasons.append(f"{entry_tf} still extending — no pullback yet")
    elif ret_quality in ("deep", "failing"):
        score -= 0.2
        reasons.append(f"{entry_tf} retracement = {ret_quality} — pullback too deep")
    elif ret_quality == "broken":
        score -= 0.5
        reasons.append(f"{entry_tf} retracement = broken — trend is over")

    # ── Sour spot: approaching major higher-TF S/R against direction ─────
    htf_sr = htf.get("sr_levels", [])
    etf_price = etf.get("current_price", 0)
    etf_atr = etf.get("atr", 0)

    if htf_sr and etf_price > 0 and etf_atr > 0:
        sour_proximity = etf_atr * 1.5  # 1.5 ATR = danger zone
        for level in htf_sr[:5]:  # check strongest levels
            lvl_price = level.get("price", 0)
            dist = abs(etf_price - lvl_price)
            if dist < sour_proximity:
                kind = level.get("kind", "")
                # Buying into resistance or selling into support = sour spot
                if direction == 1 and lvl_price > etf_price and kind in ("R", "SR"):
                    score -= 0.3
                    reasons.append(
                        f"{higher_tf} resistance at {lvl_price:.5f} "
                        f"({dist/etf_atr:.1f} ATR away) — SOUR SPOT"
                    )
                    break
                elif direction == -1 and lvl_price < etf_price and kind in ("S", "SR"):
                    score -= 0.3
                    reasons.append(
                        f"{higher_tf} support at {lvl_price:.5f} "
                        f"({dist/etf_atr:.1f} ATR away) — SOUR SPOT"
                    )
                    break

    # ── Candle confirmation at entry TF ──────────────────────────────────
    etf_candles = etf.get("candle_class", [])
    if etf_candles:
        last_candle = etf_candles[-1]
        if last_candle.get("bias") == direction:
            score += 0.1
            reasons.append(f"{entry_tf} last candle bias confirms direction")
        elif last_candle.get("type") == "WRB" and last_candle.get("bias") == -direction:
            score -= 0.2
            reasons.append(f"{entry_tf} WRB against direction")

    # ── Final classification ─────────────────────────────────────────────
    score = max(-1.0, min(1.0, score))
    result["score"] = round(score, 2)
    result["reasons"] = reasons

    if score >= 0.4:
        result["type"] = "sweet_spot"
    elif score <= -0.2:
        result["type"] = "sour_spot"
    else:
        result["type"] = "neutral"

    return result


# ═════════════════════════════════════════════════════════════════════════════
#  8. PRISTINE BUY / SELL SETUP DETECTION  (Ch. 6, 10, 12 combined)
# ═════════════════════════════════════════════════════════════════════════════

def detect_pristine_setup(
    stage: dict,
    pivot_trend: dict,
    retracement: dict,
    volume_class: dict,
    sweet_spot: dict,
    last_candle: dict,
    sr_levels: list,
    current_price: float,
    direction: int,
) -> dict | None:
    """
    Detect a formal Pristine Buy Setup (PBS) or Pristine Sell Setup (PSS).

    The textbook setup (Ch. 6, 10, 12):
      1. Higher TF is in Stage 2 (buy) or Stage 4 (sell)          — GATE
      2. Trading TF shows pivot trend in trade direction           — GATE
      3. Price is pulling back (retracement < 60%)                 — scored
      4. Pullback is to 20 EMA area or major support               — scored
      5. Bullish/bearish reversal candle at the pullback location  — scored
      6. Volume declining on pullback (no selling pressure)         — scored
      7. Entry TF is in sweet spot (multi-TF alignment)            — scored

    Quality grading:
      A+: 7/7 criteria met
      A:  6/7 criteria met
      B:  5/7 criteria met (minimum tradeable)
      Below B: rejected
    """
    criteria_met = []
    criteria_missed = []

    # ── 1. Stage Gate (HARD REQUIREMENT) ─────────────────────────────────
    stage_num = stage.get("stage", 0)
    if direction == 1 and stage_num == 2:
        criteria_met.append("Stage 2 uptrend (higher TF)")
    elif direction == -1 and stage_num == 4:
        criteria_met.append("Stage 4 downtrend (higher TF)")
    else:
        criteria_missed.append(f"Stage {stage_num} — wrong stage for {'BUY' if direction == 1 else 'SELL'}")

    # ── 2. Pivot Trend Gate (HARD REQUIREMENT) ───────────────────────────
    pv_trend = pivot_trend.get("trend", "range")
    if (direction == 1 and pv_trend == "uptrend") or \
       (direction == -1 and pv_trend == "downtrend"):
        criteria_met.append(f"Pivot trend = {pv_trend}")
    elif pv_trend == "range":
        criteria_missed.append(f"Pivot trend = range (need {('uptrend' if direction == 1 else 'downtrend')})")
    else:
        criteria_missed.append(f"Pivot trend = {pv_trend} (wrong direction)")

    # ── 3. Retracement quality ───────────────────────────────────────────
    ret_q = retracement.get("quality", "unknown")
    if ret_q in ("pristine", "healthy"):
        criteria_met.append(f"Retracement = {ret_q} ({retracement.get('retracement_pct', 0):.0%})")
    elif ret_q == "deep":
        criteria_met.append(f"Retracement = deep ({retracement.get('retracement_pct', 0):.0%}) — acceptable")
    elif ret_q == "none":
        criteria_missed.append("No pullback — price still extending")
    else:
        criteria_missed.append(f"Retracement = {ret_q} — too deep")

    # ── 4. Pullback to MA or S/R ─────────────────────────────────────────
    at_ma = retracement.get("near_ma20", False) or retracement.get("near_ma40", False)
    at_sr = False
    if sr_levels and current_price > 0:
        for lvl in sr_levels[:5]:
            lvl_price = lvl.get("price", 0)
            if direction == 1 and lvl.get("kind") in ("S", "SR"):
                if current_price > 0 and abs(current_price - lvl_price) / current_price < 0.005:
                    at_sr = True
                    break
            elif direction == -1 and lvl.get("kind") in ("R", "SR"):
                if current_price > 0 and abs(current_price - lvl_price) / current_price < 0.005:
                    at_sr = True
                    break

    if at_ma or at_sr:
        loc = []
        if at_ma:
            loc.append("MA area")
        if at_sr:
            loc.append("S/R level")
        criteria_met.append(f"Pullback to {' + '.join(loc)}")
    else:
        criteria_missed.append("Pullback not at MA or S/R level")

    # ── 5. Reversal candle signal ────────────────────────────────────────
    candle_bias = last_candle.get("bias", 0)
    candle_type = last_candle.get("type", "normal")
    if candle_bias == direction:
        desc = candle_type
        if last_candle.get("tail") == "demand_rejection" and direction == 1:
            desc = "demand rejection (hammer)"
        elif last_candle.get("tail") == "supply_rejection" and direction == -1:
            desc = "supply rejection"
        criteria_met.append(f"Reversal candle: {desc}")
    else:
        criteria_missed.append("No reversal candle signal")

    # ── 6. Volume on pullback ────────────────────────────────────────────
    pb_vol = volume_class.get("pullback_vol_trend", "flat")
    if pb_vol == "declining":
        criteria_met.append("Volume declining on pullback — healthy")
    elif pb_vol == "rising":
        criteria_missed.append("Volume rising on pullback — danger")
    else:
        criteria_met.append("Volume neutral on pullback")

    # ── 7. Sweet spot ────────────────────────────────────────────────────
    spot_type = sweet_spot.get("type", "neutral")
    if spot_type == "sweet_spot":
        criteria_met.append("Multi-TF sweet spot confirmed")
    elif spot_type == "sour_spot":
        criteria_missed.append("Multi-TF sour spot — macro resistance ahead")
    else:
        criteria_met.append("Multi-TF alignment neutral")

    # ── Grade ────────────────────────────────────────────────────────────
    met_count = len(criteria_met)
    total = met_count + len(criteria_missed)

    if met_count >= 7:
        quality = "A+"
    elif met_count >= 6:
        quality = "A"
    elif met_count >= 5:
        quality = "B"
    else:
        return None  # Below minimum quality

    setup_type = "PBS" if direction == 1 else "PSS"

    # ── SL / TP from pivots (Ch. 13) ─────────────────────────────────────
    sl = 0.0
    tp = 0.0
    if direction == 1:
        # SL below the pullback low (last pivot low)
        if retracement.get("impulse_start", 0) > 0:
            sl = retracement["impulse_start"]
        # TP at the prior pivot high (or beyond)
        if retracement.get("impulse_end", 0) > 0:
            tp = retracement["impulse_end"]
    else:
        # SL above the pullback high (last pivot high)
        if retracement.get("impulse_start", 0) > 0:
            sl = retracement["impulse_start"]
        # TP at the prior pivot low (or beyond)
        if retracement.get("impulse_end", 0) > 0:
            tp = retracement["impulse_end"]

    return {
        "type": setup_type,
        "quality": quality,
        "entry_price": current_price,
        "stop_loss": sl,
        "take_profit": tp,
        "criteria_met": criteria_met,
        "criteria_missed": criteria_missed,
        "met_count": met_count,
        "total_criteria": total,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  9. BREAKOUT BAR FAILURE  (Chapter 13)
# ═════════════════════════════════════════════════════════════════════════════

def detect_breakout_bar_failure(
    df: pd.DataFrame,
    sr_levels: list[dict],
) -> list[dict]:
    """
    Detect Breakout Bar Failure (BBF) — Ch. 13.

    "When a breakout bar fails — price breaks through a level with conviction
    but immediately reverses and closes back on the other side — this is one
    of the most powerful signals.  Everyone who bought the breakout is trapped."

    Criteria:
      1. A bar's high exceeds a resistance level (or low undercuts support)
      2. The bar's CLOSE is back on the original side of the level
      3. The pattern is most powerful with a WRB or high-volume bar

    These generate COUNTER-TREND signals with very high probability.
    """
    results = []
    if df is None or len(df) < 5 or not sr_levels:
        return results

    # Check last 3 bars for BBF
    for offset in range(1, min(4, len(df))):
        bar = df.iloc[-offset]
        bar_h = bar["high"]
        bar_l = bar["low"]
        bar_c = bar["close"]
        bar_o = bar["open"]

        candle = classify_candle(df, idx=-offset)

        for level in sr_levels[:10]:  # check strongest 10 levels
            lvl_price = level.get("price", 0)
            kind = level.get("kind", "")

            # ── Bearish BBF: broke above resistance but closed below ─────
            if kind in ("R", "SR") and bar_h > lvl_price and bar_c < lvl_price:
                strength = 0.85
                if candle["type"] == "WRB":
                    strength = 0.95
                if level.get("touches", 0) >= 3:
                    strength = min(strength + 0.05, 1.0)

                results.append({
                    "type": "BBF",
                    "bias": -1,  # bearish — fade the failed breakout
                    "level": lvl_price,
                    "strength": strength,
                    "bar_offset": offset,
                    "name": f"BBF at resistance {lvl_price:.5f}",
                })

            # ── Bullish BBF: broke below support but closed above ────────
            elif kind in ("S", "SR") and bar_l < lvl_price and bar_c > lvl_price:
                strength = 0.85
                if candle["type"] == "WRB":
                    strength = 0.95
                if level.get("touches", 0) >= 3:
                    strength = min(strength + 0.05, 1.0)

                results.append({
                    "type": "BBF",
                    "bias": 1,  # bullish — fade the failed breakdown
                    "level": lvl_price,
                    "strength": strength,
                    "bar_offset": offset,
                    "name": f"BBF at support {lvl_price:.5f}",
                })

    return results


# ═════════════════════════════════════════════════════════════════════════════
#  10. PRICE VOIDS  (Chapter 3)
# ═════════════════════════════════════════════════════════════════════════════

def find_price_voids(
    df: pd.DataFrame,
    min_void_atr: float = 2.0,
) -> list[dict]:
    """
    Detect price voids — areas where price moved rapidly with no
    consolidation (Ch. 3).

    "A price void is a region that price traversed with no overlap from
    prior bars.  Price tends to move quickly through voids again."

    These are important because:
      - There's no S/R in a void (price never paused there)
      - If price enters a void, it will likely traverse it quickly
      - Voids above = less resistance; voids below = less support
    """
    results = []
    if df is None or len(df) < 20 or "atr" not in df.columns:
        return results

    atr = df["atr"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values

    for i in range(2, len(df) - 1):
        atr_val = atr[i]
        if np.isnan(atr_val) or atr_val == 0:
            continue

        body = abs(closes[i] - opens[i])
        bar_range = highs[i] - lows[i]

        # A void-creating bar: big body, moves through a range with no overlap
        if bar_range < min_void_atr * atr_val:
            continue
        if body < bar_range * 0.6:  # strong body required
            continue

        bullish = closes[i] > opens[i]

        # Check for gap / no-overlap with prior bar
        if bullish:
            # Void between prior bar's high and this bar's low (or open)
            void_low = highs[i - 1]
            void_high = lows[i]  # might overlap
            if void_high > void_low:
                results.append({
                    "void_high": float(void_high),
                    "void_low": float(void_low),
                    "direction": 1,
                    "time": df.index[i],
                    "size_atr": round((void_high - void_low) / atr_val, 2),
                })
        else:
            void_high = lows[i - 1]
            void_low = highs[i]
            if void_high > void_low:
                results.append({
                    "void_high": float(void_high),
                    "void_low": float(void_low),
                    "direction": -1,
                    "time": df.index[i],
                    "size_atr": round((void_high - void_low) / atr_val, 2),
                })

    return results


# ═════════════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _safe_ema(df: pd.DataFrame, period: int) -> np.ndarray | None:
    """Compute EMA from close prices, return as numpy array or None."""
    if df is None or len(df) < period:
        return None
    col_name = f"_pristine_ema_{period}"
    if col_name not in df.columns:
        vals = df["close"].ewm(span=period, adjust=False).mean().values
        return vals
    return df[col_name].values


def _safe_sma(df: pd.DataFrame, period: int) -> np.ndarray | None:
    """Compute SMA from close prices, return as numpy array or None."""
    if df is None or len(df) < period:
        return None
    return df["close"].rolling(window=period).mean().values


def _estimate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Estimate ATR from the last *period* bars (simple version)."""
    if df is None or len(df) < period:
        return 0.0
    if "atr" in df.columns:
        val = df["atr"].iloc[-1]
        return val if not np.isnan(val) else 0.0
    ranges = df["high"].values[-period:] - df["low"].values[-period:]
    return float(np.mean(ranges))
