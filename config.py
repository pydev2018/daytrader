"""
===============================================================================
  WOLF TRADING SYSTEM — Master Configuration
===============================================================================
  Every tunable parameter lives here.  Nothing is hard-coded elsewhere.
  Values are loaded from .env where secrets are involved; everything else
  has a sensible default that can be overridden at runtime.
===============================================================================
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env ────────────────────────────────────────────────────────────────
_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(_ENV_PATH)

# ═════════════════════════════════════════════════════════════════════════════
#  MT5 CONNECTION
# ═════════════════════════════════════════════════════════════════════════════
MT5_PATH: str = os.getenv("MT5_PATH", "")          # blank = auto-detect
_login_raw = os.getenv("MT5_LOGIN", "0").strip()
MT5_LOGIN: int = int(_login_raw) if _login_raw else 0   # 0 = use current session
MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
MT5_SERVER: str = os.getenv("MT5_SERVER", "")
MT5_TIMEOUT: int = 60_000                            # ms

# ═════════════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ═════════════════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ═════════════════════════════════════════════════════════════════════════════
#  OPENAI
# ═════════════════════════════════════════════════════════════════════════════
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = "gpt-5.2-2025-12-11"

# ═════════════════════════════════════════════════════════════════════════════
#  CAPITAL & ACCOUNT
# ═════════════════════════════════════════════════════════════════════════════
TRADING_CAPITAL: float = float(os.getenv("TRADING_CAPITAL", "1000"))
MAGIC_NUMBER: int = 777_888         # unique EA identifier for our orders

# ═════════════════════════════════════════════════════════════════════════════
#  TIMEFRAMES  (ordered from highest to lowest for multi-TF analysis)
# ═════════════════════════════════════════════════════════════════════════════
# These are the MT5 timeframe constants we will use
TIMEFRAMES_ANALYSIS = {
    "W1":  "TIMEFRAME_W1",
    "D1":  "TIMEFRAME_D1",
    "H4":  "TIMEFRAME_H4",
    "H1":  "TIMEFRAME_H1",
    "M15": "TIMEFRAME_M15",
    "M5":  "TIMEFRAME_M5",
}

# How many bars to fetch per timeframe for analysis
BARS_PER_TIMEFRAME = {
    "W1":  104,   # ~2 years
    "D1":  252,   # ~1 year
    "H4":  500,
    "H1":  720,   # ~30 days
    "M15": 672,   # ~7 days
    "M5":  576,   # ~2 days
}

# ═════════════════════════════════════════════════════════════════════════════
#  INDICATOR PARAMETERS
# ═════════════════════════════════════════════════════════════════════════════
# Moving Averages
EMA_FAST: int = 9
EMA_MEDIUM: int = 21
EMA_SLOW: int = 50
EMA_TREND: int = 200

# RSI
RSI_PERIOD: int = 14
RSI_OVERBOUGHT: float = 70.0
RSI_OVERSOLD: float = 30.0

# MACD
MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL: int = 9

# Stochastic
STOCH_K: int = 14
STOCH_D: int = 3
STOCH_SMOOTH: int = 3
STOCH_OVERBOUGHT: float = 80.0
STOCH_OVERSOLD: float = 20.0

# ADX
ADX_PERIOD: int = 14
ADX_TREND_THRESHOLD: float = 25.0   # above = trending

# Bollinger Bands
BB_PERIOD: int = 20
BB_STD: float = 2.0

# ATR
ATR_PERIOD: int = 14

# CCI
CCI_PERIOD: int = 20

# Ichimoku
ICHI_TENKAN: int = 9
ICHI_KIJUN: int = 26
ICHI_SENKOU_B: int = 52

# ═════════════════════════════════════════════════════════════════════════════
#  STRUCTURE DETECTION
# ═════════════════════════════════════════════════════════════════════════════
SR_LOOKBACK: int = 100              # bars to scan for S/R levels
SR_TOUCH_TOLERANCE_PCT: float = 0.1 # % tolerance for level touch
SR_MIN_TOUCHES: int = 2             # minimum touches to confirm level
SWING_LOOKBACK: int = 5             # bars each side for swing detection

# ═════════════════════════════════════════════════════════════════════════════
#  SMART MONEY CONCEPTS
# ═════════════════════════════════════════════════════════════════════════════
ORDER_BLOCK_LOOKBACK: int = 50
FVG_MIN_GAP_ATR_MULT: float = 0.5  # FVG must be >= 0.5x ATR to count
LIQUIDITY_SWEEP_LOOKBACK: int = 20

# ═════════════════════════════════════════════════════════════════════════════
#  PRISTINE METHOD PARAMETERS  (Greg Capra methodology)
# ═════════════════════════════════════════════════════════════════════════════
# Moving Averages as visual aids (Ch. 4)
PRISTINE_MA_FAST: int = 20        # EMA — pullback reference
PRISTINE_MA_MED: int = 40         # EMA — intermediate trend health
PRISTINE_MA_SLOW: int = 200       # SMA — institutional trend

# Pivots (Ch. 10)
PIVOT_LOOKBACK: int = 5           # bars each side for swing detection

# Retracement thresholds (Ch. 6)
RETRACEMENT_PRISTINE: float = 0.40   # < 40% = best quality pullback
RETRACEMENT_HEALTHY: float = 0.50    # 40-50% = standard pullback
RETRACEMENT_DEEP: float = 0.60       # 50-60% = deep, trend weakening
RETRACEMENT_MAX_GATE: float = 0.80   # > 80% = trend broken, reject

# Volume classification (Ch. 5)
VOL_PROFESSIONAL_THRESHOLD: float = 1.8
VOL_DECLINING_PULLBACK: float = 0.7

# Candle classification (Ch. 2)
WRB_BODY_RATIO: float = 2.0      # body > 2x avg = Wide Range Body
NRB_BODY_RATIO: float = 0.5      # body < 0.5x avg = Narrow Range Body
COG_THRESHOLD: float = 0.25      # close in top/bottom 25% of range

# Sweet/Sour spot (Ch. 12)
SOUR_SPOT_SR_PROXIMITY_ATR: float = 1.5

# PBS/PSS minimum criteria (Ch. 6, 10, 12)
PBS_MIN_CRITERIA: int = 5         # minimum 5/7 criteria for tradeable setup

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIDENCE SCORING  (weights must sum to 100)
#  Reweighted for Pristine Method: price action > indicators
# ═════════════════════════════════════════════════════════════════════════════
CONFIDENCE_WEIGHTS = {
    "stage_alignment":        20,  # Ch. 1  — THE primary gate
    "pivot_trend_quality":    15,  # Ch. 10 — pivot sequence health
    "sweet_spot_score":       15,  # Ch. 12 — multi-TF alignment quality
    "sr_level_quality":       15,  # Ch. 3  — objective S/R proximity
    "retracement_quality":    10,  # Ch. 6  — pullback depth & location
    "candle_signal_quality":  10,  # Ch. 2  — WRB/COG/Tail interpretation
    "volume_classification":   5,  # Ch. 5  — professional vs novice
    "indicator_confirmation":  5,  # Ch. 4,16 — demoted to minor aid
    "spread_quality":          5,  # practical execution concern
}
assert sum(CONFIDENCE_WEIGHTS.values()) == 100, \
    f"CONFIDENCE_WEIGHTS must sum to 100, got {sum(CONFIDENCE_WEIGHTS.values())}"
CONFIDENCE_THRESHOLD: float = 75.0   # auto-accept: full Pristine alignment
CONFIDENCE_REVIEW_BAND: float = 55.0 # review band floor: second-layer re-evaluation

# ── Two-Tier Review Band ──────────────────────────────────────────────────
#
# TIER 1: Standard Review (65-74.9)
#   Score held back by moderate weakness in 1-2 peripherals.
#   Requires strong core and at least one exceptional anchor.
#
# TIER 2: Structural Override (55-64.9)
#   Score distorted by temporal noise (S/R oscillation, candle timing).
#   Requires EXCEPTIONAL core (≥0.90 avg) + secondary confirmations.
#   This catches setups like WHEAT: stage=0.90, sweet=1.00, retrace=1.00
#   whose total score bounces 58-68 due to S/R proximity noise.
#
# Standard Review (65-74.9)
REVIEW_BAND_CORE_MIN: float = 0.80   # average(stage, sweet_spot, retrace) ≥ this
REVIEW_BAND_STAGE_MIN: float = 0.50  # macro trend must not be hostile
REVIEW_BAND_SWEET_MIN: float = 0.40  # need at least moderate multi-TF agreement
REVIEW_BAND_ANCHOR_MIN: float = 0.90 # at least one core component must be exceptional

# Structural Override (55-64.9) — much stricter structural requirements
DEEP_REVIEW_CORE_MIN: float = 0.90   # core avg must be exceptional
DEEP_REVIEW_STAGE_MIN: float = 0.70  # clear trend required
DEEP_REVIEW_SWEET_MIN: float = 0.70  # strong multi-TF agreement required
DEEP_REVIEW_SECONDARY_MIN: int = 2   # need ≥2 of {volume≥0.50, pivot≥0.30, candle≥0.40, ind≥0.50}

# ═════════════════════════════════════════════════════════════════════════════
#  GPT-5.2 VISUAL CHART ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
CHART_ANALYSIS_ENABLED: bool = True       # set False to skip (saves cost/latency)

# ═════════════════════════════════════════════════════════════════════════════
#  RISK MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════
MAX_RISK_PER_TRADE_PCT: float = 1.0       # % of trading capital
MAX_RISK_PER_TRADE_PCT_CAP: float = 2.0   # hard cap even with Kelly

# ── Per-Tier Risk Factors ─────────────────────────────────────────────────
# Review-band and structural override trades should risk LESS than auto-accept.
# These multiply MAX_RISK_PER_TRADE_PCT to determine the base risk budget.
# Chart analysis can further reduce (but never increase) via its own factor.
#
# Effective risk = MAX_RISK_PER_TRADE_PCT × tier_factor × chart_factor
#
# Examples ($1000 account, 1% base risk = $10):
#   Auto-accept, charts supportive:     1.0% × 1.0 × 1.0 = 1.00% ($10.00)
#   Standard review, charts supportive: 1.0% × 0.75 × 1.0 = 0.75% ($7.50)
#   Structural override, charts ok:     1.0% × 0.50 × 0.85= 0.42% ($4.25)
#   Structural override, charts warn:   1.0% × 0.50 × 0.50= 0.25% ($2.50)
RISK_FACTOR_AUTO_ACCEPT: float = 1.0       # full risk budget
RISK_FACTOR_STANDARD_REVIEW: float = 0.75  # 75% — good core, weak peripherals
RISK_FACTOR_DEEP_REVIEW: float = 0.50      # 50% — structural override, cautious sizing
MIN_RISK_REWARD_RATIO: float = 2.0        # don't take below 1:2
ATR_SL_MULTIPLIER: float = 1.5            # SL = 1.5 * ATR beyond structure
ATR_TP_MULTIPLIER: float = 3.0            # TP target multiplier

MAX_CONCURRENT_POSITIONS: int = 5
MAX_CORRELATED_POSITIONS: int = 2
MAX_SPREAD_PIPS: float = 5.0              # skip if spread > this

DAILY_LOSS_LIMIT_PCT: float = 3.0         # % of capital → stop for day
WEEKLY_LOSS_LIMIT_PCT: float = 6.0        # % of capital → halve size
MAX_DRAWDOWN_PCT: float = 15.0            # % → halt all trading

# Trailing stop
TRAILING_STOP_ACTIVATE_R: float = 1.0     # activate after 1R profit
TRAILING_STOP_DISTANCE_ATR: float = 1.0   # trail at 1 ATR

# Partial take-profit
PARTIAL_TP_RATIO: float = 0.5             # close 50% at first target
PARTIAL_TP_RR: float = 2.0               # first target at 1:2

# ── Pristine Position Management (Ch. 7, 12, 13) ─────────────────────────
MACRO_REFRESH_SECONDS: int = 1800         # refresh D1 macro context every 30 min
MACRO_SR_PARTIAL_ATR: float = 1.5         # partial close within 1.5 ATR of D1 S/R
STRUCTURE_SL_BUFFER_ATR: float = 0.2      # buffer beyond pivot for structural stop

# ═════════════════════════════════════════════════════════════════════════════
#  KELLY CRITERION
# ═════════════════════════════════════════════════════════════════════════════
KELLY_FRACTION: float = 0.5              # half-Kelly (conservative)
KELLY_DEFAULT_WIN_RATE: float = 0.55     # initial estimate before data
KELLY_DEFAULT_WIN_LOSS_RATIO: float = 2.0

# ═════════════════════════════════════════════════════════════════════════════
#  SCANNER
# ═════════════════════════════════════════════════════════════════════════════
SCAN_INTERVAL_SECONDS: int = 60           # full universe scan cycle
POSITION_CHECK_SECONDS: int = 10          # open position monitoring (halted mode)
TICK_CHECK_SECONDS: int = 5               # fast tick surveillance between full cycles
DAILY_SUMMARY_HOUR_UTC: int = 21          # send daily summary at 21:00 UTC

# ═════════════════════════════════════════════════════════════════════════════
#  WATCHLIST — The Professional Stalking Screen
# ═════════════════════════════════════════════════════════════════════════════
# ALL signals must go through the watchlist route.  No auto-execution bypasses.
#
# Flow:  Full Scan → Watchlist (if score ≥ threshold) → Stalk for M15 trigger
#        → Trigger fires → Chart Analysis → Risk-adjusted execution
#
# This replicates the pro-trader workflow:
#   Phase 1 (Scan)    — identify high-quality structural setups
#   Phase 2 (Stalk)   — monitor watchlisted symbols for entry triggers
#   Phase 3 (Trigger) — specific M15 candle patterns fire entry
#   Phase 4 (Execute) — chart analysis + risk adjustment + execution
WATCHLIST_SETUP_THRESHOLD: float = 55.0    # min confluence score to enter watchlist
WATCHLIST_CHECK_SECONDS: float = 15.0      # trigger check interval (seconds)
WATCHLIST_MAX_AGE_HOURS: float = 4.0       # expire stale watchlist entries
WATCHLIST_MAX_ENTRIES: int = 15            # max symbols on watchlist at once

# ═════════════════════════════════════════════════════════════════════════════
#  M15 SNIPER MODE — Event-Driven M15-Only Pipeline
# ═════════════════════════════════════════════════════════════════════════════
# When enabled, the system ignores HTF gating and runs a pure M15 sniper stack:
# fast pass → deep pass (TPR/RBH) → intrabar triggers.
SNIPER_MODE: bool = os.getenv("SNIPER_MODE", "true").lower() in ("1", "true", "yes")

# Core windows
SNIPER_FAST_PASS_BARS: int = 96           # M15 bars for fast pass
SNIPER_CONTEXT_BARS: int = 192            # M15 bars for context & pivots
SNIPER_MAJOR_LEVEL_BARS: int = 384        # M15 bars for macro levels
SNIPER_TREND_LOOKBACK_BARS: int = 40      # bars to verify HH/HL or LH/LL
SNIPER_RANGE_LOOKBACK_BARS: int = 48      # bars to define range
SNIPER_COMPRESSION_BARS: int = 96         # bars for ATR percentile
SNIPER_PIVOT_L: int = 2                   # pivot/fractal lookback (L=2 fast)

# Fast pass shortlist & intrabar watch
SNIPER_SHORTLIST_MAX: int = 20            # deep-pass candidates
SNIPER_INTRABAR_TOP_N: int = 8            # intrabar monitored symbols

# Regime confidence + hysteresis
SNIPER_REGIME_MIN_CONF: float = 0.70
SNIPER_REGIME_MIN_CONF_RELAX: float = 0.55
SNIPER_REGIME_HYSTERESIS_BARS: int = 3
SNIPER_COMPRESSION_MAX_PCT: int = 40
SNIPER_COMPRESSION_RELAX_MAX_PCT: int = 55

# Adaptive gating (relax non-safety gates after quiet periods)
SNIPER_ADAPTIVE_ENABLED: bool = True
SNIPER_ADAPTIVE_IDLE_BARS: int = 8
SNIPER_ADAPTIVE_RELAX_STEP: float = 0.05
SNIPER_ADAPTIVE_MAX_RELAX: float = 0.20

# Execution style: market_close | market_intrabar | pending | hybrid
SNIPER_EXECUTION_STYLE: str = os.getenv("SNIPER_EXECUTION_STYLE", "hybrid").lower()
SNIPER_PENDING_EXPIRY_BARS: int = 6       # expire pending orders after N bars

# Quality gates (M15-only)
SNIPER_MAX_SPREAD_ATR: float = 0.15       # spread <= 0.15*ATR
SNIPER_MIN_STOP_ATR: float = 0.6          # minimum stop distance in ATR
SNIPER_NO_CHASE_ATR: float = 0.8          # entry must be within 0.8*ATR of trigger

# TPR parameters
TPR_PULLBACK_ATR: float = 0.5
TPR_INVALIDATION_ATR: float = 0.15
TPR_SL_BUFFER_ATR: float = 0.2
TPR_TRIGGER_BODY_ATR: float = 0.4
TPR_REJECTION_ENABLED: bool = True
TPR_EXPIRY_BARS: int = 6
TPR_COOLDOWN_BARS: int = 4

# RBH parameters
RBH_RANGE_WIDTH_ATR: float = 1.2
RBH_TOUCH_TOL_ATR: float = 0.25
RBH_BREAK_BUFFER_ATR: float = 0.1
RBH_BREAK_BODY_ATR: float = 0.35
RBH_RETEST_TOL_ATR: float = 0.15
RBH_RETEST_WINDOW_BARS: int = 8
RBH_SL_BUFFER_ATR: float = 0.1
RBH_COOLDOWN_BARS: int = 6

# ECR parameters (EMA Cycle Reversion)
ECR_FAST_EMA: int = 5
ECR_SIGNAL_EMA: int = 13
ECR_TREND_EMA: int = 50
ECR_TARGET_EMA: int = 200
ECR_CROSS_COUNT: int = 3
ECR_CROSS_WINDOW_BARS: int = 60
ECR_CROSS_MIN_GAP_BARS: int = 4
ECR_ENTRY_BODY_ATR: float = 0.40
ECR_MAX_TARGET_ATR: float = 2.5
ECR_MAX_EMA50_SLOPE_ATR: float = 0.20
ECR_MAX_SPREAD_ATR: float = 0.12
ECR_STOP_LOOKBACK: int = 12
ECR_SL_BUFFER_ATR: float = 0.2
ECR_MIN_SCORE: float = 70.0
ECR_RISK_FACTOR: float = 0.60
ECR_SESSION_ONLY: bool = True
ECR_ALLOWED_SESSIONS: list[str] = ["Tokyo", "Sydney"]
ECR_TREND_VETO_CONF: float = 0.75

# Asset class detection (for per-asset thresholds)
SNIPER_METALS_PREFIXES: list[str] = ["XAU", "XAG", "XPT", "XPD"]
SNIPER_INDEX_KEYWORDS: list[str] = [
    "US30", "US500", "US100", "NAS100", "UK100",
    "DE40", "JP225", "FR40", "EU50", "AU200",
]
SNIPER_COMMODITY_KEYWORDS: list[str] = [
    "OIL", "NATGAS", "COPPER", "CORN", "WHEAT", "SUGAR",
]

# Per-asset-class overrides (only list differences from defaults)
SNIPER_ASSET_CLASS_OVERRIDES = {
    "fx": {},
    "metals": {
        "min_stop_atr": 0.8,
        "no_chase_atr": 1.0,
        "tpr_trigger_body_atr": 0.50,
        "rbh_break_body_atr": 0.45,
        "regime_min_conf": 0.75,
        "compression_max_pct": 35,
        "ecr_min_score": 75.0,
        "ecr_max_target_atr": 2.0,
        "ecr_risk_factor": 0.50,
    },
    "indices": {
        "min_stop_atr": 0.8,
        "no_chase_atr": 0.95,
        "tpr_trigger_body_atr": 0.50,
        "rbh_break_body_atr": 0.45,
        "compression_max_pct": 35,
    },
    "commodities": {
        "min_stop_atr": 0.9,
        "no_chase_atr": 1.0,
        "tpr_trigger_body_atr": 0.55,
        "rbh_break_body_atr": 0.50,
        "regime_min_conf": 0.75,
        "compression_max_pct": 35,
    },
    "crypto": {
        "min_stop_atr": 1.2,
        "no_chase_atr": 1.2,
        "max_spread_atr": 0.20,
        "tpr_rejection_enabled": False,
        "tpr_trigger_body_atr": 0.60,
        "rbh_break_body_atr": 0.60,
        "regime_min_conf": 0.80,
        "compression_max_pct": 30,
        "ecr_enabled": False,
    },
}

# TP policy
# structure = swing target + R-multiple, r_multiple = fixed R targets
SNIPER_TP_POLICY: str = os.getenv("SNIPER_TP_POLICY", "structure").lower()
SNIPER_TP_R1: float = 1.5
SNIPER_TP_R2: float = 2.5

# Scoring weights (must sum to 100)
TPR_SCORE_WEIGHTS = {
    "structure": 25,
    "ema": 20,
    "pullback": 20,
    "spread": 15,
    "momentum": 20,
}
RBH_SCORE_WEIGHTS = {
    "range": 25,
    "compression": 20,
    "break": 20,
    "retest": 20,
    "spread": 15,
}
ECR_SCORE_WEIGHTS = {
    "cycle": 25,
    "trend": 20,
    "ema200": 20,
    "spread": 15,
    "momentum": 20,
}
assert sum(TPR_SCORE_WEIGHTS.values()) == 100
assert sum(RBH_SCORE_WEIGHTS.values()) == 100
assert sum(ECR_SCORE_WEIGHTS.values()) == 100

# Position monitoring (sniper)
SNIPER_MONITOR_TF: str = "M5"
SNIPER_MONITOR_LOOKBACK: int = 60

# Asset classes to scan (Oanda symbol groups — matched to actual names)
# Oanda uses names like EURUSD.sml, XAUUSD.sml, USOIL.sml, US30, DE40, etc.
SCAN_GROUPS = [
    "*USD*", "*EUR*", "*GBP*", "*JPY*", "*AUD*",
    "*NZD*", "*CAD*", "*CHF*", "*XAU*", "*XAG*",
    "*US30*", "*US500*", "*US100*", "*UK100*", "*DE40*",
    "*JP225*", "*FR40*", "*EU50*", "*AU200*",
    "*OIL*", "*BTC*", "*ETH*", "*SOL*", "*DOGE*",
    "*COPPER*", "*NATGAS*", "*CORN*", "*WHEAT*", "*SUGAR*",
]

# Symbols to always exclude (illiquid / exotic)
EXCLUDE_SYMBOLS: list[str] = []

# ═════════════════════════════════════════════════════════════════════════════
#  CRYPTO — Traded 24/7, exempt from forex session and weekend restrictions
# ═════════════════════════════════════════════════════════════════════════════
# Known crypto symbol prefixes (matched against the start of the symbol name).
# Any symbol whose base currency starts with one of these is classified as
# crypto and exempted from forex market-hours, Friday wind-down, and weekend
# close logic.
CRYPTO_PREFIXES: list[str] = [
    "BTC", "ETH", "LTC", "BCH", "XRP", "SOL", "DOGE", "ADA",
    "BNB", "DOT", "EOS", "LINK", "UNI", "XLM", "XTZ", "AVAX",
    "MATIC", "GLMR", "KSM",
]

# ═════════════════════════════════════════════════════════════════════════════
#  MARKET SESSIONS (UTC hours)
# ═════════════════════════════════════════════════════════════════════════════
SESSIONS = {
    "Sydney":  {"open": 21, "close": 6},
    "Tokyo":   {"open": 0,  "close": 9},
    "London":  {"open": 7,  "close": 16},
    "NewYork": {"open": 13, "close": 22},
}

# Best sessions per currency (crypto is handled separately — always allowed)
CURRENCY_SESSIONS = {
    "AUD": ["Sydney", "Tokyo"],
    "NZD": ["Sydney", "Tokyo"],
    "JPY": ["Tokyo", "London"],
    "EUR": ["London", "NewYork"],
    "GBP": ["London", "NewYork"],
    "USD": ["London", "NewYork"],
    "CAD": ["NewYork"],
    "CHF": ["London"],
    "XAU": ["London", "NewYork"],
    "XAG": ["London", "NewYork"],
    "OIL": ["London", "NewYork"],
}

# ═════════════════════════════════════════════════════════════════════════════
#  CORRELATION GROUPS  (symbols within same group are correlated)
# ═════════════════════════════════════════════════════════════════════════════
CORRELATION_GROUPS = [
    ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],          # USD-counter
    ["USDCHF", "USDJPY", "USDCAD"],                     # USD-base
    ["EURJPY", "GBPJPY", "AUDJPY"],                     # JPY-crosses
    ["XAUUSD", "XAGUSD"],                                # metals
    ["USOIL", "UKOIL"],                                  # oil
    ["US30", "US500", "NAS100"],                          # US indices
    ["BTCUSD", "BTCJPY"],                                 # BTC pair correlation
    ["ETHUSD", "ETHJPY"],                                 # ETH pair correlation
    ["BTCUSD", "ETHUSD", "SOLUSD"],                       # major crypto (highly correlated)
]

# ═════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ═════════════════════════════════════════════════════════════════════════════
LOG_LEVEL: str = "INFO"
LOG_DIR: Path = Path(__file__).parent / "logs"
TRADE_JOURNAL_PATH: Path = Path(__file__).parent / "data" / "trade_journal.json"

# ═════════════════════════════════════════════════════════════════════════════
#  PATHS
# ═════════════════════════════════════════════════════════════════════════════
BASE_DIR: Path = Path(__file__).parent
