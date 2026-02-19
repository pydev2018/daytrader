"""OCO breakout runtime configuration."""

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

_oco_symbols_raw = os.getenv("OCO_SYMBOLS", "").strip()
if _oco_symbols_raw:
    OCO_SYMBOLS: list[str] = [s.strip() for s in _oco_symbols_raw.split(",") if s.strip()]
else:
    OCO_SYMBOLS = ["EURUSD"]

OCO_TIMEFRAME: str = os.getenv("OCO_TIMEFRAME", "M5")

# Size & inventory controls
BASE_ORDER_SIZE_LOTS: float = float(os.getenv("BASE_ORDER_SIZE_LOTS", "0.01"))

# ═════════════════════════════════════════════════════════════════════════════
#  EXECUTION CONTROL
# ═════════════════════════════════════════════════════════════════════════════
OCO_LOOP_SECONDS: float = float(os.getenv("OCO_LOOP_SECONDS", "2.0"))
OCO_SETUP_REFRESH_SECONDS: float = float(os.getenv("OCO_SETUP_REFRESH_SECONDS", "10.0"))
OCO_TELEMETRY_INTERVAL_SECONDS: float = float(
    os.getenv("OCO_TELEMETRY_INTERVAL_SECONDS", "120.0")
)
OCO_ADAPTIVE_LOOP_ENABLED: bool = os.getenv(
    "OCO_ADAPTIVE_LOOP_ENABLED", "true"
).lower() in ("true", "1", "yes")
OCO_LOOP_MIN_SECONDS: float = float(os.getenv("OCO_LOOP_MIN_SECONDS", "0.5"))
OCO_LOOP_MAX_SECONDS: float = float(os.getenv("OCO_LOOP_MAX_SECONDS", "5.0"))
OCO_SMART_SETUP_REFRESH_ENABLED: bool = os.getenv(
    "OCO_SMART_SETUP_REFRESH_ENABLED", "true"
).lower() in ("true", "1", "yes")
OCO_SETUP_SPREAD_SHOCK_RATIO: float = float(
    os.getenv("OCO_SETUP_SPREAD_SHOCK_RATIO", "2.0")
)

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

OCO_REENTRY_COOLDOWN_SECONDS: int = int(os.getenv("OCO_REENTRY_COOLDOWN_SECONDS", "900"))

OCO_BREAKOUT_LOOKBACK: int = int(os.getenv("OCO_BREAKOUT_LOOKBACK", "80"))
OCO_ARM_TTL_SECONDS: int = int(os.getenv("OCO_ARM_TTL_SECONDS", "1800"))
OCO_BUFFER_ATR_MULT: float = float(os.getenv("OCO_BUFFER_ATR_MULT", "0.35"))
OCO_BUFFER_SPREAD_MULT: float = float(os.getenv("OCO_BUFFER_SPREAD_MULT", "3.0"))

OCO_SIZE_MULT: float = float(os.getenv("OCO_SIZE_MULT", "1.0"))
OCO_SL_SPACING_MULT: float = float(os.getenv("OCO_SL_SPACING_MULT", "2.0"))
OCO_BREAK_EVEN_ACTIVATE_R: float = float(os.getenv("OCO_BREAK_EVEN_ACTIVATE_R", "0.25"))
OCO_BREAK_EVEN_OFFSET_POINTS: float = float(os.getenv("OCO_BREAK_EVEN_OFFSET_POINTS", "2.0"))
OCO_TRAIL_ACTIVATE_R: float = float(os.getenv("OCO_TRAIL_ACTIVATE_R", "0.9"))
OCO_TRAIL_SPACING_MULT: float = float(os.getenv("OCO_TRAIL_SPACING_MULT", "1.2"))
OCO_SL_MIN_DISTANCE_BUFFER_POINTS: float = float(
    os.getenv("OCO_SL_MIN_DISTANCE_BUFFER_POINTS", "2.0")
)
OCO_TIME_STOP_SECONDS: int = int(os.getenv("OCO_TIME_STOP_SECONDS", "7200"))

# ═════════════════════════════════════════════════════════════════════════════
#  STATE PATHS
# ═════════════════════════════════════════════════════════════════════════════
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
    if OCO_LOOP_SECONDS < 0.1:
        errors.append(f"OCO_LOOP_SECONDS must be >= 0.1 (got {OCO_LOOP_SECONDS})")
    if OCO_SETUP_REFRESH_SECONDS < 1.0:
        errors.append(
            f"OCO_SETUP_REFRESH_SECONDS must be >= 1.0 "
            f"(got {OCO_SETUP_REFRESH_SECONDS})"
        )
    if OCO_LOOP_MIN_SECONDS < 0.1:
        errors.append(
            f"OCO_LOOP_MIN_SECONDS must be >= 0.1 "
            f"(got {OCO_LOOP_MIN_SECONDS})"
        )
    if OCO_LOOP_MAX_SECONDS < OCO_LOOP_MIN_SECONDS:
        errors.append(
            "OCO_LOOP_MAX_SECONDS must be >= OCO_LOOP_MIN_SECONDS "
            f"(got {OCO_LOOP_MAX_SECONDS} < {OCO_LOOP_MIN_SECONDS})"
        )
    if OCO_SETUP_SPREAD_SHOCK_RATIO < 1.0:
        errors.append(
            f"OCO_SETUP_SPREAD_SHOCK_RATIO must be >= 1.0 "
            f"(got {OCO_SETUP_SPREAD_SHOCK_RATIO})"
        )
    if OCO_TELEMETRY_INTERVAL_SECONDS < 10.0:
        errors.append(
            f"OCO_TELEMETRY_INTERVAL_SECONDS must be >= 10.0 "
            f"(got {OCO_TELEMETRY_INTERVAL_SECONDS})"
        )
    if STOP_LOSS_COOLDOWN_SECONDS < 0:
        errors.append(
            f"STOP_LOSS_COOLDOWN_SECONDS must be >= 0 "
            f"(got {STOP_LOSS_COOLDOWN_SECONDS})"
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
    if OCO_BREAK_EVEN_ACTIVATE_R <= 0:
        errors.append(
            f"OCO_BREAK_EVEN_ACTIVATE_R must be > 0 "
            f"(got {OCO_BREAK_EVEN_ACTIVATE_R})"
        )
    if OCO_BREAK_EVEN_OFFSET_POINTS < 0:
        errors.append(
            f"OCO_BREAK_EVEN_OFFSET_POINTS must be >= 0 "
            f"(got {OCO_BREAK_EVEN_OFFSET_POINTS})"
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
    if OCO_SL_MIN_DISTANCE_BUFFER_POINTS < 0:
        errors.append(
            "OCO_SL_MIN_DISTANCE_BUFFER_POINTS must be >= 0 "
            f"(got {OCO_SL_MIN_DISTANCE_BUFFER_POINTS})"
        )
    if OCO_TIME_STOP_SECONDS < 60:
        errors.append(
            f"OCO_TIME_STOP_SECONDS must be >= 60 "
            f"(got {OCO_TIME_STOP_SECONDS})"
        )
    if OCO_REENTRY_COOLDOWN_SECONDS < 0:
        errors.append(
            f"OCO_REENTRY_COOLDOWN_SECONDS must be >= 0 "
            f"(got {OCO_REENTRY_COOLDOWN_SECONDS})"
        )
    if not OCO_SYMBOLS:
        errors.append("OCO_SYMBOLS must have at least one symbol")

    if errors:
        print("=" * 60, file=sys.stderr)
        print("  CONFIGURATION ERRORS — System cannot start", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(1)


_validate()
