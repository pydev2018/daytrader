from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = DATA_DIR / "state"

_ENV_PATH = BASE_DIR / ".env"
load_dotenv(_ENV_PATH)

MT5_PATH: str = os.getenv("MT5_PATH", "")
MT5_LOGIN: int = int((os.getenv("MT5_LOGIN", "0") or "0").strip() or "0")
MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
MT5_SERVER: str = os.getenv("MT5_SERVER", "")
MT5_TIMEOUT_MS: int = int(os.getenv("MT5_TIMEOUT_MS", "60000"))

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
MAGIC_NUMBER: int = int(os.getenv("MAGIC_NUMBER", "929292"))

_symbols_raw = os.getenv("SYMBOLS", "EURCAD").strip()
SYMBOLS: list[str] = [s.strip() for s in _symbols_raw.split(",") if s.strip()]
TIMEFRAME: str = os.getenv("TIMEFRAME", "M5")
BAR_COUNT: int = int(os.getenv("BAR_COUNT", "500"))
LOOP_SECONDS: float = float(os.getenv("LOOP_SECONDS", "2.0"))

LOT_SIZE: float = float(os.getenv("LOT_SIZE", "0.01"))
START_WITH_ANCHOR: bool = os.getenv("START_WITH_ANCHOR", "true").lower() in ("true", "1", "yes")
ANCHOR_LOT_MULT: float = float(os.getenv("ANCHOR_LOT_MULT", "1.0"))
MAX_RUNGS_PER_SIDE: int = int(os.getenv("MAX_RUNGS_PER_SIDE", "6"))
OFFSET_RATIO: float = float(os.getenv("OFFSET_RATIO", "0.5"))
STEP_ATR_MULT: float = float(os.getenv("STEP_ATR_MULT", "0.9"))
STEP_SPREAD_MULT: float = float(os.getenv("STEP_SPREAD_MULT", "12.0"))
MIN_STEP_TICKS: int = int(os.getenv("MIN_STEP_TICKS", "5"))
RECENTER_MOVE_STEPS: float = float(os.getenv("RECENTER_MOVE_STEPS", "1.5"))

ATR_PERIOD: int = int(os.getenv("ATR_PERIOD", "14"))
ADX_PERIOD: int = int(os.getenv("ADX_PERIOD", "14"))
TREND_FAST_EMA: int = int(os.getenv("TREND_FAST_EMA", "34"))
TREND_SLOW_EMA: int = int(os.getenv("TREND_SLOW_EMA", "89"))
TREND_ON_ADX: float = float(os.getenv("TREND_ON_ADX", "25.0"))
TREND_OFF_ADX: float = float(os.getenv("TREND_OFF_ADX", "20.0"))
EXHAUSTION_CONFIRM_BARS: int = int(os.getenv("EXHAUSTION_CONFIRM_BARS", "4"))
SLOW_TREND_BARS: int = int(os.getenv("SLOW_TREND_BARS", "5"))
SLOW_TREND_MIN_MOVE_STEPS: float = float(os.getenv("SLOW_TREND_MIN_MOVE_STEPS", "1.0"))

MAX_SPREAD_PIPS: float = float(os.getenv("MAX_SPREAD_PIPS", "3.0"))
MAX_MARGIN_USAGE_PCT: float = float(os.getenv("MAX_MARGIN_USAGE_PCT", "70.0"))
MAX_DRAWDOWN_PCT: float = float(os.getenv("MAX_DRAWDOWN_PCT", "18.0"))
MAX_NET_DELTA_LOTS: float = float(os.getenv("MAX_NET_DELTA_LOTS", "0.50"))
CLEANUP_CLOSE_COUNT: int = int(os.getenv("CLEANUP_CLOSE_COUNT", "2"))
RISK_OFF_UNWIND_PER_CYCLE: int = int(os.getenv("RISK_OFF_UNWIND_PER_CYCLE", "2"))
PROTECT_OSCILLATION_BANK: bool = os.getenv("PROTECT_OSCILLATION_BANK", "true").lower() in ("true", "1", "yes")

STATE_PATH = STATE_DIR / "phased_grid_state.json"


def _validate() -> None:
    errors: list[str] = []
    if not SYMBOLS:
        errors.append("SYMBOLS must not be empty")
    if LOT_SIZE <= 0:
        errors.append(f"LOT_SIZE must be > 0 (got {LOT_SIZE})")
    if ANCHOR_LOT_MULT <= 0:
        errors.append(f"ANCHOR_LOT_MULT must be > 0 (got {ANCHOR_LOT_MULT})")
    if MAX_RUNGS_PER_SIDE < 1:
        errors.append(f"MAX_RUNGS_PER_SIDE must be >= 1 (got {MAX_RUNGS_PER_SIDE})")
    if not (0.1 <= OFFSET_RATIO <= 1.0):
        errors.append(f"OFFSET_RATIO must be in [0.1, 1.0] (got {OFFSET_RATIO})")
    if STEP_ATR_MULT <= 0 or STEP_SPREAD_MULT <= 0:
        errors.append("STEP_ATR_MULT and STEP_SPREAD_MULT must be > 0")
    if MIN_STEP_TICKS < 1:
        errors.append(f"MIN_STEP_TICKS must be >= 1 (got {MIN_STEP_TICKS})")
    if TREND_ON_ADX <= TREND_OFF_ADX:
        errors.append("TREND_ON_ADX must be greater than TREND_OFF_ADX")
    if EXHAUSTION_CONFIRM_BARS < 1:
        errors.append("EXHAUSTION_CONFIRM_BARS must be >= 1")
    if SLOW_TREND_BARS < 1:
        errors.append("SLOW_TREND_BARS must be >= 1")
    if SLOW_TREND_MIN_MOVE_STEPS <= 0:
        errors.append("SLOW_TREND_MIN_MOVE_STEPS must be > 0")
    if LOOP_SECONDS < 0.2:
        errors.append("LOOP_SECONDS must be >= 0.2")
    if MAX_MARGIN_USAGE_PCT <= 0 or MAX_MARGIN_USAGE_PCT > 100:
        errors.append("MAX_MARGIN_USAGE_PCT must be in (0, 100]")
    if MAX_DRAWDOWN_PCT <= 0 or MAX_DRAWDOWN_PCT > 100:
        errors.append("MAX_DRAWDOWN_PCT must be in (0, 100]")
    if CLEANUP_CLOSE_COUNT < 1:
        errors.append("CLEANUP_CLOSE_COUNT must be >= 1")
    if RISK_OFF_UNWIND_PER_CYCLE < 1:
        errors.append("RISK_OFF_UNWIND_PER_CYCLE must be >= 1")

    if errors:
        raise SystemExit("Configuration error(s):\n- " + "\n- ".join(errors))


_validate()
