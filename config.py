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
CONFIDENCE_REVIEW_BAND: float = 65.0 # review band floor: second-layer re-evaluation

# ── Review Band: Core Strength Parameters ─────────────────────────────────
# Trades scoring [REVIEW_BAND, THRESHOLD) are re-evaluated using the three
# Pristine components that best predict trade success: Stage alignment,
# Sweet Spot (multi-TF agreement), and Retracement Quality.
# Peripheral weaknesses (S/R proximity, exact pivots, volume, indicators)
# are forgiven IF the core is genuinely strong.
REVIEW_BAND_CORE_MIN: float = 0.80   # average(stage, sweet_spot, retrace) must be ≥ this
REVIEW_BAND_STAGE_MIN: float = 0.50  # macro trend must not be hostile
REVIEW_BAND_SWEET_MIN: float = 0.40  # need at least moderate multi-TF agreement
REVIEW_BAND_ANCHOR_MIN: float = 0.90 # at least one core component must be exceptional

# ═════════════════════════════════════════════════════════════════════════════
#  RISK MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════
MAX_RISK_PER_TRADE_PCT: float = 1.0       # % of trading capital
MAX_RISK_PER_TRADE_PCT_CAP: float = 2.0   # hard cap even with Kelly
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
#  MARKET SESSIONS (UTC hours)
# ═════════════════════════════════════════════════════════════════════════════
SESSIONS = {
    "Sydney":  {"open": 21, "close": 6},
    "Tokyo":   {"open": 0,  "close": 9},
    "London":  {"open": 7,  "close": 16},
    "NewYork": {"open": 13, "close": 22},
}

# Best sessions per currency
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
