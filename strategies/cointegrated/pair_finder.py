"""Cointegrated pair discovery and diagnostics (Institutional Grade)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from brokers.mt5 import MT5Broker
from config import settings as cfg
from utils.logger import get_logger

log = get_logger("cointegrated_pair_finder")

try:
    from statsmodels.tsa.stattools import adfuller
    from statsmodels.tsa.vector_ar.vecm import coint_johansen
    from statsmodels.tsa.vector_ar.var_model import VAR

    HAS_STATSMODELS = True
except Exception:
    HAS_STATSMODELS = False


@dataclass
class PairCheckResult:
    symbol_x: str
    symbol_y: str
    timeframe: str
    bars: int
    beta: float | None
    intercept: float | None
    adf_pvalue: float | None
    adf_stat: float | None
    johansen_trace_ratio: float | None
    johansen_pass: bool | None
    hurst: float | None
    half_life_bars: float | None
    rolling_pass_ratio: float | None
    pass_engle_granger: bool
    pass_all: bool
    notes: list[str]


class PairFinder:
    def __init__(self, broker: MT5Broker):
        self.broker = broker

    @staticmethod
    def dependency_ok() -> tuple[bool, str]:
        if HAS_STATSMODELS:
            return True, "ok"
        return (
            False,
            "Missing dependency: statsmodels. Install in tradebot env: conda run -n tradebot pip install statsmodels",
        )

    def check_pair(self, symbol_x: str, symbol_y: str, timeframe: str, bars: int) -> PairCheckResult:
        notes: list[str] = []

        if not HAS_STATSMODELS:
            notes.append("statsmodels unavailable")
            return self._empty_result(symbol_x, symbol_y, timeframe, bars, notes)

        x = self._load_log_close(symbol_x, timeframe, bars)
        y = self._load_log_close(symbol_y, timeframe, bars)
        
        if x is None or y is None:
            notes.append("missing data for one or both symbols")
            return self._empty_result(symbol_x, symbol_y, timeframe, bars, notes)

        pair_df = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
        if len(pair_df) < max(200, cfg.COINT_ROLLING_WINDOW + 10):
            notes.append(f"insufficient overlapping bars: {len(pair_df)}")
            return self._empty_result(symbol_x, symbol_y, timeframe, bars, notes)

        # 1. Strict I(1) Pre-Check: Both assets must be non-stationary individually
        _, p_x = self._adf(pair_df["x"].values)
        _, p_y = self._adf(pair_df["y"].values)
        
        pass_i1 = True
        if p_x is not None and p_x <= cfg.COINT_ADF_ALPHA:
            notes.append(f"{symbol_x} is already stationary (I(0)). Invalid for cointegration.")
            pass_i1 = False
        if p_y is not None and p_y <= cfg.COINT_ADF_ALPHA:
            notes.append(f"{symbol_y} is already stationary (I(0)). Invalid for cointegration.")
            pass_i1 = False

        # 2. TLS Spread Calculation (Orthogonal Regression)
        beta, intercept, spread = self._tls_spread(pair_df["x"].values, pair_df["y"].values)
        
        # 3. ADF Test on the Spread (I(0) Check)
        adf_stat, adf_p = self._adf(spread)
        pass_engle = bool(adf_p is not None and adf_p <= cfg.COINT_ADF_ALPHA)

        # 4. Dynamic Johansen Test
        j_trace_ratio, j_pass = self._dynamic_johansen(pair_df)
        
        # 5. Tradability Metrics
        hurst = self._hurst_exponent(spread)
        half_life = self._half_life(spread)
        rolling_ratio = self._rolling_tls_ratio(pair_df)

        # 6. Strict Threshold Validations
        pass_hurst = hurst is not None and hurst <= cfg.COINT_MAX_HURST
        pass_half_life = half_life is not None and half_life <= cfg.COINT_MAX_HALF_LIFE_BARS
        pass_rolling = rolling_ratio is not None and rolling_ratio >= cfg.COINT_MIN_ROLLING_PASS_RATIO
        pass_johansen = bool(j_pass) if j_pass is not None else False

        # The ultimate institutional gatekeeper
        pass_all = pass_i1 and pass_engle and pass_johansen and pass_hurst and pass_half_life and pass_rolling

        # Populate diagnostic notes
        if pass_i1 and not pass_engle:
            notes.append("Spread ADF failed (not I(0))")
        if pass_i1 and not pass_johansen:
            notes.append("Johansen trace test failed")
        if not pass_hurst:
            notes.append("Hurst too high (weak mean-reversion)")
        if not pass_half_life:
            notes.append("half-life too long or infinite")
        if not pass_rolling:
            notes.append("rolling stability too low")

        return PairCheckResult(
            symbol_x=symbol_x,
            symbol_y=symbol_y,
            timeframe=timeframe,
            bars=len(pair_df),
            beta=beta,
            intercept=intercept,
            adf_pvalue=adf_p,
            adf_stat=adf_stat,
            johansen_trace_ratio=j_trace_ratio,
            johansen_pass=j_pass,
            hurst=hurst,
            half_life_bars=half_life,
            rolling_pass_ratio=rolling_ratio,
            pass_engle_granger=pass_engle,
            pass_all=pass_all,
            notes=notes,
        )

    def _empty_result(self, x: str, y: str, tf: str, bars: int, notes: list[str]) -> PairCheckResult:
        """Helper to return an empty result structure seamlessly."""
        return PairCheckResult(
            symbol_x=x, symbol_y=y, timeframe=tf, bars=bars,
            beta=None, intercept=None, adf_pvalue=None, adf_stat=None,
            johansen_trace_ratio=None, johansen_pass=None, hurst=None,
            half_life_bars=None, rolling_pass_ratio=None,
            pass_engle_granger=False, pass_all=False, notes=notes,
        )

    def run_and_persist(self, symbol_x: str, symbol_y: str, timeframe: str, bars: int) -> PairCheckResult:
        result = self.check_pair(symbol_x, symbol_y, timeframe, bars)
        out_dir = cfg.DATA_DIR / "pair_checks"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"pair_check_{symbol_x}_{symbol_y}_{stamp}.json"
        latest = out_dir / "latest_pair_check.json"
        payload = asdict(result)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log.info(f"Pair-check report saved: {out_path}")
        return result

    def _load_log_close(self, symbol: str, timeframe: str, bars: int) -> pd.Series | None:
        if not self.broker.select_symbol(symbol):
            log.warning(f"Cannot select symbol {symbol}")
            return None
        df = self.broker.get_rates(symbol, timeframe, bars)
        if df is None or df.empty:
            log.warning(f"No rates for {symbol} timeframe={timeframe} bars={bars}")
            return None
        close = df[["time", "close"]].copy()
        close = close.dropna()
        close = close[close["close"] > 0]
        if close.empty:
            return None
        close["log_close"] = np.log(close["close"].astype(float))
        return close.set_index("time")["log_close"]

    @staticmethod
    def _tls_spread(x: np.ndarray, y: np.ndarray) -> tuple[float, float, np.ndarray]:
        """
        Total Least Squares (TLS) implementation using Singular Value Decomposition.
        Symmetrically accounts for variance in both x and y.
        """
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        
        # Mean-center the data
        x_mean, y_mean = np.mean(x), np.mean(y)
        matrix = np.vstack((x - x_mean, y - y_mean)).T
        
        # Apply SVD
        _, _, Vt = np.linalg.svd(matrix, full_matrices=False)
        
        # The optimal orthogonal vector is the last row of V^T
        v = Vt[-1, :]
        
        # Calculate beta and intercept from the singular vector
        # v[0] * (x - x_mean) + v[1] * (y - y_mean) = 0
        beta = float(-v[1] / v[0]) if v[0] != 0 else 0.0
        intercept = float(x_mean - beta * y_mean)
        
        spread = x - (intercept + beta * y)
        return beta, intercept, spread

    @staticmethod
    def _adf(series: np.ndarray) -> tuple[float | None, float | None]:
        try:
            stat, pvalue, *_ = adfuller(series, autolag="AIC")
            return float(stat), float(pvalue)
        except Exception:
            return None, None

    @staticmethod
    def _dynamic_johansen(df: pd.DataFrame) -> tuple[float | None, bool | None]:
        """
        Johansen test with dynamic lag selection via Vector Autoregression (AIC).
        """
        try:
            data = df[["x", "y"]].values
            
            # Differenced data is used to find the optimal VAR lag for I(1) series
            diff_data = np.diff(data, axis=0)
            model = VAR(diff_data)
            
            # Select best lag length minimizing AIC, cap at 10 to avoid overfitting
            var_res = model.fit(maxlags=10, ic='aic')
            k_ar_diff = max(1, var_res.k_ar) # Johansen needs at least 1 lag

            test = coint_johansen(data, det_order=0, k_ar_diff=k_ar_diff)
            trace = float(test.lr1[0])
            crit_95 = float(test.cvt[0, 1])
            ratio = trace / crit_95 if crit_95 > 0 else float("nan")
            return ratio, bool(trace > crit_95)
        except Exception as e:
            log.debug(f"Johansen dynamic fail: {e}")
            return None, None

    @staticmethod
    def _half_life(spread: np.ndarray) -> float | None:
        try:
            s = np.asarray(spread, dtype=float)
            lag = s[:-1]
            delta = np.diff(s)
            design = np.column_stack([np.ones(len(lag)), lag])
            params, *_ = np.linalg.lstsq(design, delta, rcond=None)
            b = float(params[1])
            if b >= 0:
                return float("inf")
            return float(-math.log(2.0) / b)
        except Exception:
            return None

    @staticmethod
    def _hurst_exponent(series: np.ndarray) -> float | None:
        try:
            x = np.asarray(series, dtype=float)
            if len(x) < 120:
                return None
            lags = range(2, 20)
            tau = [np.std(x[lag:] - x[:-lag]) for lag in lags]
            tau = np.asarray(tau)
            valid = tau > 0
            if not np.any(valid):
                return None
            slope, _ = np.polyfit(np.log(np.array(list(lags))[valid]), np.log(tau[valid]), 1)
            return float(slope)
        except Exception:
            return None

    def _rolling_tls_ratio(self, pair_df: pd.DataFrame) -> float | None:
        """
        Calculates consistency strictly using rolling TLS windows.
        """
        window = cfg.COINT_ROLLING_WINDOW
        step = cfg.COINT_ROLLING_STEP
        if len(pair_df) < window:
            return None

        passes = 0
        total = 0
        x_all = pair_df["x"].values
        y_all = pair_df["y"].values
        
        for start in range(0, len(pair_df) - window + 1, step):
            end = start + window
            x_win = x_all[start:end]
            y_win = y_all[start:end]
            
            # Use TLS for the rolling window to match the new strict architecture
            beta, intercept, spread = self._tls_spread(x_win, y_win)
            
            _, p = self._adf(spread)
            if p is not None and p <= cfg.COINT_ADF_ALPHA:
                passes += 1
            total += 1

        return float(passes / total) if total > 0 else None


def format_pair_result(result: PairCheckResult) -> str:
    def _fmt(v: Any, nd: int = 4) -> str:
        if v is None:
            return "NA"
        if isinstance(v, bool):
            return "PASS" if v else "FAIL"
        if isinstance(v, float):
            if math.isinf(v):
                return "inf"
            return f"{v:.{nd}f}"
        return str(v)

    lines = [
        "=" * 84,
        f"PAIR CHECK  {result.symbol_x} / {result.symbol_y}  tf={result.timeframe}  bars={result.bars}",
        "=" * 84,
        f"beta (TLS):            {_fmt(result.beta)}",
        f"intercept (TLS):       {_fmt(result.intercept)}",
        f"ADF stat:              {_fmt(result.adf_stat)}",
        f"ADF p-value:           {_fmt(result.adf_pvalue)}  (alpha={cfg.COINT_ADF_ALPHA})",
        f"Johansen trace/95%:    {_fmt(result.johansen_trace_ratio)}",
        f"Johansen pass:         {_fmt(result.johansen_pass)}",
        f"Hurst exponent:        {_fmt(result.hurst)}  (max={cfg.COINT_MAX_HURST})",
        f"Half-life (bars):      {_fmt(result.half_life_bars)}  (max={cfg.COINT_MAX_HALF_LIFE_BARS})",
        f"Rolling pass ratio:    {_fmt(result.rolling_pass_ratio)}  (min={cfg.COINT_MIN_ROLLING_PASS_RATIO})",
        f"Engle-Granger pass:    {_fmt(result.pass_engle_granger)}",
        f"FINAL verdict:         {_fmt(result.pass_all)}",
    ]
    if result.notes:
        lines.append("notes:")
        for n in result.notes:
            lines.append(f"  - {n}")
    lines.append("=" * 84)
    return "\n".join(lines)