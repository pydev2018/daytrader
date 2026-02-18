"""
===============================================================================
  GRID TRADING SYSTEM — Configuration
===============================================================================
  All tunable parameters live here. Secrets are read from .env.
  Added: parameter validation at import time (S2 #16).
===============================================================================
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# ── Base paths ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = DATA_DIR / "state"

# ── Load .env ────────────────────────────────────────────────────────────────
_ENV_PATH = BASE_DIR / ".env"
load_dotenv(_ENV_PATH)

# ═════════════════════════════════════════════════════════════════════════════
#  MT5 CONNECTION
# ═════════════════════════════════════════════════════════════════════════════
MT5_PATH: str = os.getenv("MT5_PATH", "")
_login_raw = os.getenv("MT5_LOGIN", "0").strip()
MT5_LOGIN: int = int(_login_raw) if _login_raw else 0
MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
MT5_SERVER: str = os.getenv("MT5_SERVER", "")
MT5_TIMEOUT: int = 60_000  # ms

# ═════════════════════════════════════════════════════════════════════════════
#  TELEGRAM ALERTS
# ═════════════════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ═════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ═════════════════════════════════════════════════════════════════════════════
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ═════════════════════════════════════════════════════════════════════════════
#  RUNTIME / IDENTITIES
# ═════════════════════════════════════════════════════════════════════════════
MAGIC_NUMBER: int = int(os.getenv("MAGIC_NUMBER", "778899"))

# ═════════════════════════════════════════════════════════════════════════════
#  SYMBOLS & FEEDS
# ═════════════════════════════════════════════════════════════════════════════
_symbols_raw = os.getenv("GRID_SYMBOLS", "EURUSD").strip()
GRID_SYMBOLS: list[str] = [s.strip() for s in _symbols_raw.split(",") if s.strip()]

GRID_TIMEFRAME: str = os.getenv("GRID_TIMEFRAME", "M1")  # bars for anchor/vol
GRID_BAR_COUNT: int = int(os.getenv("GRID_BAR_COUNT", "500"))

# ═════════════════════════════════════════════════════════════════════════════
#  GRID CORE PARAMETERS
# ═════════════════════════════════════════════════════════════════════════════
GRID_LEVELS: int = int(os.getenv("GRID_LEVELS", "6"))  # per side
GRID_RESET_K: float = float(os.getenv("GRID_RESET_K", "2.0"))
GRID_RESET_MIN_SECONDS: int = int(os.getenv("GRID_RESET_MIN_SECONDS", "60"))

# Anchor (EMA) and volatility
ANCHOR_HALFLIFE_SECONDS: int = int(os.getenv("ANCHOR_HALFLIFE_SECONDS", "300"))
VOL_EWMA_LAMBDA: float = float(os.getenv("VOL_EWMA_LAMBDA", "0.97"))
VOL_HORIZON_SECONDS: int = int(os.getenv("VOL_HORIZON_SECONDS", "120"))

# Spacing (vol + cost)
GRID_SPACING_K_SIGMA: float = float(os.getenv("GRID_SPACING_K_SIGMA", "1.2"))
GRID_SPACING_K_COST: float = float(os.getenv("GRID_SPACING_K_COST", "1.6"))
GRID_SPACING_MIN_TICKS: int = int(os.getenv("GRID_SPACING_MIN_TICKS", "3"))
SLIPPAGE_BUFFER_TICKS: int = int(os.getenv("SLIPPAGE_BUFFER_TICKS", "1"))
EDGE_MIN_TICKS: int = int(os.getenv("EDGE_MIN_TICKS", "1"))

# Size & inventory controls
BASE_ORDER_SIZE_LOTS: float = float(os.getenv("BASE_ORDER_SIZE_LOTS", "0.01"))
SIZE_TAPER_ETA: float = float(os.getenv("SIZE_TAPER_ETA", "0.15"))
INVENTORY_SKEW_GAMMA: float = float(os.getenv("INVENTORY_SKEW_GAMMA", "0.5"))
CENTER_SKEW_K: float = float(os.getenv("CENTER_SKEW_K", "0.8"))

# Stop-loss: cut losing rungs if price moves this many spacings against entry
# 0 = disabled. 3.0 = stop at 3x spacing away from entry.
RUNG_STOP_LOSS_SPACINGS: float = float(os.getenv("RUNG_STOP_LOSS_SPACINGS", "3.0"))

# Pre-weekend: close EXIT rungs whose take-profit is further than this
# many spacings from current price. Protects against weekend gaps.
# Positions close to their TP are kept (they'll likely fill Monday).
# 2.0 = close if exit is more than 2 spacings away from current price.
WEEKEND_CLOSE_THRESHOLD_SPACINGS: float = float(os.getenv("WEEKEND_CLOSE_THRESHOLD_SPACINGS", "2.0"))

# ═════════════════════════════════════════════════════════════════════════════
#  EXECUTION CONTROL
# ═════════════════════════════════════════════════════════════════════════════
GRID_LOOP_SECONDS: float = float(os.getenv("GRID_LOOP_SECONDS", "2.0"))
ORDER_REFRESH_SECONDS: float = float(os.getenv("ORDER_REFRESH_SECONDS", "10.0"))
REQUOTE_THRESHOLD_TICKS: int = int(os.getenv("REQUOTE_THRESHOLD_TICKS", "2"))
MAX_PENDING_ORDERS_PER_SYMBOL: int = int(os.getenv("MAX_PENDING_ORDERS_PER_SYMBOL", "30"))

# ═════════════════════════════════════════════════════════════════════════════
#  SCANNER & AUTO-ROTATION
# ═════════════════════════════════════════════════════════════════════════════
AUTO_SCAN_ENABLED: bool = os.getenv("AUTO_SCAN_ENABLED", "true").lower() in ("true", "1", "yes")
SCAN_INTERVAL_SECONDS: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "1800"))  # 30 min
SCAN_TIMEFRAME: str = os.getenv("SCAN_TIMEFRAME", "M5")
SCAN_BAR_COUNT: int = int(os.getenv("SCAN_BAR_COUNT", "500"))
MAX_ACTIVE_SYMBOLS: int = int(os.getenv("MAX_ACTIVE_SYMBOLS", "3"))
MIN_SCAN_SCORE: float = float(os.getenv("MIN_SCAN_SCORE", "40.0"))

# Scanner universe: if GRID_SYMBOLS is set, scan only those.
# If SCAN_UNIVERSE is set, scan that full list and auto-pick the best.
_scan_universe_raw = os.getenv("SCAN_UNIVERSE", "").strip()
SCAN_UNIVERSE: list[str] = [s.strip() for s in _scan_universe_raw.split(",") if s.strip()]

# ═════════════════════════════════════════════════════════════════════════════
#  REGIME / SAFETY GATES
# ═════════════════════════════════════════════════════════════════════════════
SPREAD_PAUSE_MULT: float = float(os.getenv("SPREAD_PAUSE_MULT", "3.0"))
VOL_SHOCK_RATIO: float = float(os.getenv("VOL_SHOCK_RATIO", "2.0"))
TREND_SLOPE_Z: float = float(os.getenv("TREND_SLOPE_Z", "2.0"))
TREND_HARD_PAUSE_MULT: float = float(os.getenv("TREND_HARD_PAUSE_MULT", "1.5"))
TREND_CONFIRM_BARS: int = int(os.getenv("TREND_CONFIRM_BARS", "3"))
RANGE_CONFIRM_BARS: int = int(os.getenv("RANGE_CONFIRM_BARS", "6"))

# ═════════════════════════════════════════════════════════════════════════════
#  RISK LIMITS
# ═════════════════════════════════════════════════════════════════════════════
MAX_LEVERAGE: float = float(os.getenv("MAX_LEVERAGE", "5.0"))
MAX_DRAWDOWN_PCT: float = float(os.getenv("MAX_DRAWDOWN_PCT", "15.0"))
DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3.0"))
WEEKLY_LOSS_LIMIT_PCT: float = float(os.getenv("WEEKLY_LOSS_LIMIT_PCT", "6.0"))
MAX_INVENTORY_LOTS: float = float(os.getenv("MAX_INVENTORY_LOTS", "1.0"))
MAX_NOTIONAL_MULT_EQUITY: float = float(os.getenv("MAX_NOTIONAL_MULT_EQUITY", "3.0"))
STOP_LOSS_COOLDOWN_SECONDS: int = int(os.getenv("STOP_LOSS_COOLDOWN_SECONDS", "600"))

# ═════════════════════════════════════════════════════════════════════════════
#  HYBRID GRID + OCO BREAKOUT MODE
# ═════════════════════════════════════════════════════════════════════════════
HYBRID_ENABLED: bool = os.getenv("HYBRID_ENABLED", "true").lower() in ("true", "1", "yes")
HYBRID_FLATTEN_GRID_ON_TREND: bool = os.getenv("HYBRID_FLATTEN_GRID_ON_TREND", "true").lower() in ("true", "1", "yes")
HYBRID_REENTRY_COOLDOWN_SECONDS: int = int(os.getenv("HYBRID_REENTRY_COOLDOWN_SECONDS", "900"))

OCO_BREAKOUT_LOOKBACK: int = int(os.getenv("OCO_BREAKOUT_LOOKBACK", "80"))
OCO_ARM_TTL_SECONDS: int = int(os.getenv("OCO_ARM_TTL_SECONDS", "1800"))
OCO_BUFFER_ATR_MULT: float = float(os.getenv("OCO_BUFFER_ATR_MULT", "0.35"))
OCO_BUFFER_SPREAD_MULT: float = float(os.getenv("OCO_BUFFER_SPREAD_MULT", "3.0"))

OCO_SIZE_MULT: float = float(os.getenv("OCO_SIZE_MULT", "1.0"))
OCO_SL_SPACING_MULT: float = float(os.getenv("OCO_SL_SPACING_MULT", "2.0"))
OCO_TRAIL_ACTIVATE_R: float = float(os.getenv("OCO_TRAIL_ACTIVATE_R", "0.9"))
OCO_TRAIL_SPACING_MULT: float = float(os.getenv("OCO_TRAIL_SPACING_MULT", "1.2"))
OCO_TIME_STOP_SECONDS: int = int(os.getenv("OCO_TIME_STOP_SECONDS", "7200"))

# ═════════════════════════════════════════════════════════════════════════════
#  BACKTEST-SPECIFIC (used only by backtest engines)
# ═════════════════════════════════════════════════════════════════════════════
BT_COMMISSION_PER_LOT: float = float(os.getenv("BT_COMMISSION_PER_LOT", "0.0"))  # OANDA: no commission, cost in spread
BT_SWAP_PER_LOT_PER_DAY: float = float(os.getenv("BT_SWAP_PER_LOT_PER_DAY", "0.5"))
BT_DEFAULT_SLIPPAGE_TICKS: int = int(os.getenv("BT_DEFAULT_SLIPPAGE_TICKS", "1"))

# ═════════════════════════════════════════════════════════════════════════════
#  STATE PATHS
# ═════════════════════════════════════════════════════════════════════════════
GRID_STATE_PATH = STATE_DIR / "grid_state.json"
RISK_STATE_PATH = STATE_DIR / "risk_state.json"
TRADE_JOURNAL_PATH = DATA_DIR / "trade_journal.jsonl"

# ═════════════════════════════════════════════════════════════════════════════
#  PARAMETER VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _validate():
    """Validate configuration parameters at startup.

    Raises SystemExit with a clear message for any invalid value.
    This prevents the system from starting with nonsensical parameters
    that could cause silent failures or unexpected losses.
    """
    errors: list[str] = []

    if GRID_LEVELS < 1:
        errors.append(f"GRID_LEVELS must be >= 1 (got {GRID_LEVELS})")
    if GRID_LEVELS > 50:
        errors.append(f"GRID_LEVELS > 50 is excessive (got {GRID_LEVELS})")
    if GRID_RESET_K <= 0:
        errors.append(f"GRID_RESET_K must be > 0 (got {GRID_RESET_K})")
    if GRID_RESET_MIN_SECONDS < 0:
        errors.append(
            f"GRID_RESET_MIN_SECONDS must be >= 0 (got {GRID_RESET_MIN_SECONDS})"
        )
    if BASE_ORDER_SIZE_LOTS <= 0:
        errors.append(f"BASE_ORDER_SIZE_LOTS must be > 0 (got {BASE_ORDER_SIZE_LOTS})")
    if MAX_INVENTORY_LOTS <= 0:
        errors.append(f"MAX_INVENTORY_LOTS must be > 0 (got {MAX_INVENTORY_LOTS})")
    if MAX_DRAWDOWN_PCT <= 0 or MAX_DRAWDOWN_PCT > 100:
        errors.append(f"MAX_DRAWDOWN_PCT must be in (0, 100] (got {MAX_DRAWDOWN_PCT})")
    if DAILY_LOSS_LIMIT_PCT <= 0 or DAILY_LOSS_LIMIT_PCT > 100:
        errors.append(f"DAILY_LOSS_LIMIT_PCT must be in (0, 100] (got {DAILY_LOSS_LIMIT_PCT})")
    if WEEKLY_LOSS_LIMIT_PCT <= 0 or WEEKLY_LOSS_LIMIT_PCT > 100:
        errors.append(f"WEEKLY_LOSS_LIMIT_PCT must be in (0, 100] (got {WEEKLY_LOSS_LIMIT_PCT})")
    if MAX_LEVERAGE <= 0:
        errors.append(f"MAX_LEVERAGE must be > 0 (got {MAX_LEVERAGE})")
    if GRID_SPACING_K_SIGMA <= 0:
        errors.append(f"GRID_SPACING_K_SIGMA must be > 0 (got {GRID_SPACING_K_SIGMA})")
    if GRID_SPACING_K_COST < 1.0:
        errors.append(f"GRID_SPACING_K_COST must be >= 1.0 to survive costs (got {GRID_SPACING_K_COST})")
    if GRID_SPACING_MIN_TICKS < 1:
        errors.append(f"GRID_SPACING_MIN_TICKS must be >= 1 (got {GRID_SPACING_MIN_TICKS})")
    if EDGE_MIN_TICKS < 0:
        errors.append(f"EDGE_MIN_TICKS must be >= 0 (got {EDGE_MIN_TICKS})")
    if VOL_EWMA_LAMBDA <= 0 or VOL_EWMA_LAMBDA >= 1.0:
        errors.append(f"VOL_EWMA_LAMBDA must be in (0, 1) (got {VOL_EWMA_LAMBDA})")
    if ANCHOR_HALFLIFE_SECONDS < 1:
        errors.append(f"ANCHOR_HALFLIFE_SECONDS must be >= 1 (got {ANCHOR_HALFLIFE_SECONDS})")
    if VOL_HORIZON_SECONDS < 1:
        errors.append(f"VOL_HORIZON_SECONDS must be >= 1 (got {VOL_HORIZON_SECONDS})")
    if GRID_LOOP_SECONDS < 0.1:
        errors.append(f"GRID_LOOP_SECONDS must be >= 0.1 (got {GRID_LOOP_SECONDS})")
    if ORDER_REFRESH_SECONDS < 0.1:
        errors.append(
            f"ORDER_REFRESH_SECONDS must be >= 0.1 (got {ORDER_REFRESH_SECONDS})"
        )
    if REQUOTE_THRESHOLD_TICKS < 0:
        errors.append(
            f"REQUOTE_THRESHOLD_TICKS must be >= 0 (got {REQUOTE_THRESHOLD_TICKS})"
        )
    if MAX_PENDING_ORDERS_PER_SYMBOL < 1:
        errors.append(
            f"MAX_PENDING_ORDERS_PER_SYMBOL must be >= 1 (got {MAX_PENDING_ORDERS_PER_SYMBOL})"
        )
    if INVENTORY_SKEW_GAMMA < 0:
        errors.append(f"INVENTORY_SKEW_GAMMA must be >= 0 (got {INVENTORY_SKEW_GAMMA})")
    if SIZE_TAPER_ETA < 0:
        errors.append(f"SIZE_TAPER_ETA must be >= 0 (got {SIZE_TAPER_ETA})")
    if RUNG_STOP_LOSS_SPACINGS < 0:
        errors.append(
            f"RUNG_STOP_LOSS_SPACINGS must be >= 0 (got {RUNG_STOP_LOSS_SPACINGS})"
        )
    if WEEKEND_CLOSE_THRESHOLD_SPACINGS < 0:
        errors.append(
            f"WEEKEND_CLOSE_THRESHOLD_SPACINGS must be >= 0 "
            f"(got {WEEKEND_CLOSE_THRESHOLD_SPACINGS})"
        )
    if TREND_HARD_PAUSE_MULT < 1.0:
        errors.append(
            f"TREND_HARD_PAUSE_MULT must be >= 1.0 (got {TREND_HARD_PAUSE_MULT})"
        )
    if TREND_CONFIRM_BARS < 1:
        errors.append(
            f"TREND_CONFIRM_BARS must be >= 1 (got {TREND_CONFIRM_BARS})"
        )
    if RANGE_CONFIRM_BARS < 1:
        errors.append(
            f"RANGE_CONFIRM_BARS must be >= 1 (got {RANGE_CONFIRM_BARS})"
        )
    if STOP_LOSS_COOLDOWN_SECONDS < 0:
        errors.append(
            f"STOP_LOSS_COOLDOWN_SECONDS must be >= 0 "
            f"(got {STOP_LOSS_COOLDOWN_SECONDS})"
        )
    if HYBRID_REENTRY_COOLDOWN_SECONDS < 0:
        errors.append(
            f"HYBRID_REENTRY_COOLDOWN_SECONDS must be >= 0 "
            f"(got {HYBRID_REENTRY_COOLDOWN_SECONDS})"
        )
    if OCO_BREAKOUT_LOOKBACK < 20:
        errors.append(
            f"OCO_BREAKOUT_LOOKBACK must be >= 20 "
            f"(got {OCO_BREAKOUT_LOOKBACK})"
        )
    if OCO_ARM_TTL_SECONDS < 30:
        errors.append(
            f"OCO_ARM_TTL_SECONDS must be >= 30 "
            f"(got {OCO_ARM_TTL_SECONDS})"
        )
    if OCO_BUFFER_ATR_MULT <= 0:
        errors.append(
            f"OCO_BUFFER_ATR_MULT must be > 0 "
            f"(got {OCO_BUFFER_ATR_MULT})"
        )
    if OCO_BUFFER_SPREAD_MULT < 1.0:
        errors.append(
            f"OCO_BUFFER_SPREAD_MULT must be >= 1.0 "
            f"(got {OCO_BUFFER_SPREAD_MULT})"
        )
    if OCO_SIZE_MULT <= 0:
        errors.append(f"OCO_SIZE_MULT must be > 0 (got {OCO_SIZE_MULT})")
    if OCO_SL_SPACING_MULT <= 0:
        errors.append(
            f"OCO_SL_SPACING_MULT must be > 0 "
            f"(got {OCO_SL_SPACING_MULT})"
        )
    if OCO_TRAIL_ACTIVATE_R <= 0:
        errors.append(
            f"OCO_TRAIL_ACTIVATE_R must be > 0 "
            f"(got {OCO_TRAIL_ACTIVATE_R})"
        )
    if OCO_TRAIL_SPACING_MULT <= 0:
        errors.append(
            f"OCO_TRAIL_SPACING_MULT must be > 0 "
            f"(got {OCO_TRAIL_SPACING_MULT})"
        )
    if OCO_TIME_STOP_SECONDS < 60:
        errors.append(
            f"OCO_TIME_STOP_SECONDS must be >= 60 "
            f"(got {OCO_TIME_STOP_SECONDS})"
        )
    if AUTO_SCAN_ENABLED:
        if SCAN_INTERVAL_SECONDS < 60:
            errors.append(
                f"SCAN_INTERVAL_SECONDS must be >= 60 (got {SCAN_INTERVAL_SECONDS})"
            )
        if SCAN_BAR_COUNT < 50:
            errors.append(f"SCAN_BAR_COUNT must be >= 50 (got {SCAN_BAR_COUNT})")
        if MAX_ACTIVE_SYMBOLS < 1:
            errors.append(f"MAX_ACTIVE_SYMBOLS must be >= 1 (got {MAX_ACTIVE_SYMBOLS})")
        if MIN_SCAN_SCORE < 0 or MIN_SCAN_SCORE > 100:
            errors.append(
                f"MIN_SCAN_SCORE must be in [0, 100] (got {MIN_SCAN_SCORE})"
            )
    if not GRID_SYMBOLS:
        errors.append("GRID_SYMBOLS must have at least one symbol")

    if errors:
        print("=" * 60, file=sys.stderr)
        print("  CONFIGURATION ERRORS — System cannot start", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(1)


_validate()
