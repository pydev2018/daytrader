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

# ═════════════════════════════════════════════════════════════════════════════
#  RISK LIMITS
# ═════════════════════════════════════════════════════════════════════════════
MAX_LEVERAGE: float = float(os.getenv("MAX_LEVERAGE", "5.0"))
MAX_DRAWDOWN_PCT: float = float(os.getenv("MAX_DRAWDOWN_PCT", "15.0"))
DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3.0"))
WEEKLY_LOSS_LIMIT_PCT: float = float(os.getenv("WEEKLY_LOSS_LIMIT_PCT", "6.0"))
MAX_INVENTORY_LOTS: float = float(os.getenv("MAX_INVENTORY_LOTS", "1.0"))
MAX_NOTIONAL_MULT_EQUITY: float = float(os.getenv("MAX_NOTIONAL_MULT_EQUITY", "3.0"))

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
    if VOL_EWMA_LAMBDA <= 0 or VOL_EWMA_LAMBDA >= 1.0:
        errors.append(f"VOL_EWMA_LAMBDA must be in (0, 1) (got {VOL_EWMA_LAMBDA})")
    if ANCHOR_HALFLIFE_SECONDS < 1:
        errors.append(f"ANCHOR_HALFLIFE_SECONDS must be >= 1 (got {ANCHOR_HALFLIFE_SECONDS})")
    if GRID_LOOP_SECONDS < 0.1:
        errors.append(f"GRID_LOOP_SECONDS must be >= 0.1 (got {GRID_LOOP_SECONDS})")
    if INVENTORY_SKEW_GAMMA < 0:
        errors.append(f"INVENTORY_SKEW_GAMMA must be >= 0 (got {INVENTORY_SKEW_GAMMA})")
    if SIZE_TAPER_ETA < 0:
        errors.append(f"SIZE_TAPER_ETA must be >= 0 (got {SIZE_TAPER_ETA})")
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
