#!/usr/bin/env python3
"""
Alpha Hunter Sniper Scanner - Single Script Version
No training required. Uses real-time statistical features to detect M5 reversals.
Connects to MetaTrader 5, scans symbols, and alerts when conditions align.
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import sys
import os
from pathlib import Path
import logging
from typing import Optional, Tuple
from scipy.stats import gaussian_kde
import pywt
from filterpy.kalman import KalmanFilter

# ================== CONFIGURATION ==================
# Load .env if present (optional)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass

# MT5 credentials (defaults allow using the currently logged-in terminal)
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0") or 0)
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", "")

# Symbols to scan (comma-separated in env: SCAN_SYMBOLS)
_symbols_raw = os.getenv("SCAN_SYMBOLS", "EURCAD")
SYMBOLS = [s.strip().upper() for s in _symbols_raw.split(",") if s.strip()]

# Scanner settings
SCAN_INTERVAL = 5        # seconds between scans
FUSION_THRESHOLD = 0.75  # composite score above this triggers alert

# ================== LOGGING SETUP ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("AlphaHunter")

# ================== MT5 CLIENT ==================
class MT5Client:
    """Simple wrapper for MT5 data access."""
    def __init__(self, login, password, server, path):
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        self.connected = False

    def connect(self):
        kwargs = {}
        if self.path:
            kwargs["path"] = self.path
        if self.login:
            kwargs["login"] = self.login
        if self.password:
            kwargs["password"] = self.password
        if self.server:
            kwargs["server"] = self.server
        if not mt5.initialize(**kwargs):
            logger.error(f"MT5 init failed: {mt5.last_error()}")
            return False
        self.connected = True
        logger.info("Connected to MT5")
        return True

    def disconnect(self):
        mt5.shutdown()
        self.connected = False
        logger.info("Disconnected from MT5")

    def get_rates(self, symbol, timeframe, count=1000):
        """Fetch OHLC rates as DataFrame."""
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        return df

    def get_ticks(self, symbol, lookback_seconds=60):
        """Fetch recent ticks as DataFrame."""
        from_time = datetime.now() - timedelta(seconds=lookback_seconds)
        ticks = mt5.copy_ticks_from(symbol, from_time, 10000, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(ticks)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        df.sort_index(inplace=True)
        return df

    def get_orderbook(self, symbol):
        """Fetch current market depth (if available)."""
        # MT5 provides depth via market_book_get, but not all brokers support it.
        # We'll return empty if not available.
        try:
            book = mt5.market_book_get(symbol)
            if book is None:
                return pd.DataFrame()
            # Convert to DataFrame
            bids = [{'price': b.price, 'volume': b.volume, 'type': 'bid'} for b in book if b.type == 1]
            asks = [{'price': a.price, 'volume': a.volume, 'type': 'ask'} for a in book if a.type == 2]
            df = pd.DataFrame(bids + asks)
            return df
        except:
            return pd.DataFrame()

    def symbol_info(self, symbol):
        return mt5.symbol_info(symbol)


def _resolve_symbol(symbol: str) -> str:
    """Resolve broker-specific symbol suffixes (e.g., .sml)."""
    if mt5.symbol_info(symbol) is not None:
        return symbol
    for suffix in (".sml", ".pro", ".mini"):
        candidate = f"{symbol}{suffix}"
        if mt5.symbol_info(candidate) is not None:
            return candidate
    return symbol


def _tick_price_series(tick_df: pd.DataFrame) -> pd.Series:
    """Return a reliable tick price series (last if valid, else mid)."""
    if tick_df.empty:
        return pd.Series(dtype=float)

    if "last" in tick_df.columns:
        last = tick_df["last"].astype(float)
        if (last > 0).any():
            if "bid" in tick_df.columns and "ask" in tick_df.columns:
                mid = (tick_df["bid"].astype(float) + tick_df["ask"].astype(float)) / 2.0
                last = last.mask(last <= 0, mid)
            return last.dropna()

    if "bid" in tick_df.columns and "ask" in tick_df.columns:
        return ((tick_df["bid"].astype(float) + tick_df["ask"].astype(float)) / 2.0).dropna()
    if "bid" in tick_df.columns:
        return tick_df["bid"].astype(float).dropna()
    if "ask" in tick_df.columns:
        return tick_df["ask"].astype(float).dropna()
    return pd.Series(dtype=float)

# ================== FEATURE EXTRACTORS ==================
class OrderBookTopography:
    """3D liquidity surface from order book."""
    def __init__(self, bandwidth=0.5):
        self.bandwidth = bandwidth

    def compute(self, orderbook_df, current_price):
        if orderbook_df.empty or len(orderbook_df) < 5:
            return 0.0
        bids = orderbook_df[orderbook_df['type'] == 'bid']
        asks = orderbook_df[orderbook_df['type'] == 'ask']
        if len(bids) < 2 or len(asks) < 2:
            return 0.0

        # Create KDE for bids and asks
        try:
            kde_bid = gaussian_kde(bids['price'].values, weights=bids['volume'].values, bw_method=self.bandwidth)
            kde_ask = gaussian_kde(asks['price'].values, weights=asks['volume'].values, bw_method=self.bandwidth)
        except:
            return 0.0

        # Evaluate density around current price
        price_range = np.linspace(current_price * 0.999, current_price * 1.001, 50)
        dens_bid = kde_bid(price_range)
        dens_ask = kde_ask(price_range)

        # Find local extrema
        net = dens_ask - dens_bid
        grad = np.gradient(net)
        # Reversal signal when price near a local maximum of net (ask dominance) with negative gradient
        idx = np.argmin(np.abs(price_range - current_price))
        if grad[idx] < 0 and net[idx] > 0:
            return min(1.0, abs(grad[idx]) * 10)
        return 0.0

class WaveletPhaseCoherence:
    """Detect harmonic alignment across M1/M5/M15."""
    def __init__(self, wavelet: str = 'morl', min_period_min: int = 20, max_period_min: int = 120):
        self.wavelet = wavelet
        self.min_period_min = min_period_min
        self.max_period_min = max_period_min

    def _scales_for_tf(self, tf_minutes: int) -> np.ndarray:
        min_scale = max(1, int(self.min_period_min / tf_minutes))
        max_scale = max(min_scale + 1, int(self.max_period_min / tf_minutes))
        return np.arange(min_scale, max_scale + 1)

    def _phase_and_strength(self, prices: np.ndarray, tf_minutes: int) -> Optional[Tuple[float, float]]:
        if len(prices) < 20:
            return None
        scales = self._scales_for_tf(tf_minutes)
        coeffs, _ = pywt.cwt(prices, scales, self.wavelet, 1.0)
        energy = np.abs(coeffs) ** 2
        mean_energy = energy.mean(axis=1)
        idx = int(np.argmax(mean_energy))
        phase = float(np.angle(coeffs[idx, -1]) % (2 * np.pi))
        strength = float(mean_energy[idx] / max(np.median(mean_energy), 1e-12))
        return phase, strength

    @staticmethod
    def _circ_dist(a: float, b: float) -> float:
        return abs(np.arctan2(np.sin(a - b), np.cos(a - b)))

    def _turning_score(self, phase: float) -> float:
        targets = [0.0, np.pi / 2, np.pi, 3 * np.pi / 2]
        dist = min(self._circ_dist(phase, t) for t in targets)
        return max(0.0, 1.0 - dist / (np.pi / 2))

    def compute(self, prices_m1, prices_m5, prices_m15):
        # Align by time window (last N minutes)
        lookback_minutes = 240  # 4 hours
        if len(prices_m1) < 120 or len(prices_m5) < 24 or len(prices_m15) < 12:
            return 0.0
        m1 = prices_m1[-min(len(prices_m1), lookback_minutes):]
        m5 = prices_m5[-min(len(prices_m5), lookback_minutes // 5):]
        m15 = prices_m15[-min(len(prices_m15), lookback_minutes // 15):]

        try:
            p1 = self._phase_and_strength(np.asarray(m1), 1)
            p5 = self._phase_and_strength(np.asarray(m5), 5)
            p15 = self._phase_and_strength(np.asarray(m15), 15)
            if not p1 or not p5 or not p15:
                return 0.0

            phase1, s1 = p1
            phase5, s5 = p5
            phase15, s15 = p15

            # Alignment score (0..1)
            d12 = self._circ_dist(phase1, phase5)
            d15 = self._circ_dist(phase1, phase15)
            d25 = self._circ_dist(phase5, phase15)
            align = 1.0 - (d12 + d15 + d25) / (3 * np.pi)

            # Turning-point score
            turn = (self._turning_score(phase1)
                    + self._turning_score(phase5)
                    + self._turning_score(phase15)) / 3.0

            # Energy strength (downweight noisy phases)
            min_strength = min(s1, s5, s15)
            strength = max(0.0, min(1.0, (min_strength - 1.0) / 1.5))

            score = 0.7 * align + 0.3 * turn
            return score * (0.5 + 0.5 * strength)
        except Exception:
            return 0.0

class LiquidityEntropy:
    """Shannon entropy of order book depth."""
    def __init__(self, window=20):
        self.history = []
        self.window = window

    def compute(self, orderbook_df):
        if orderbook_df.empty:
            return 0.0
        volumes = orderbook_df['volume'].values
        if len(volumes) == 0:
            return 0.0
        probs = volumes / volumes.sum()
        probs = probs[probs > 0]
        entropy = -np.sum(probs * np.log2(probs))
        self.history.append(entropy)
        if len(self.history) > self.window:
            self.history.pop(0)
        # Cliff detection: entropy drop > 20%
        if len(self.history) >= 5:
            recent = np.mean(self.history[-3:])
            older = np.mean(self.history[:-3]) if len(self.history) > 3 else recent
            if older > 0 and (older - recent) / older > 0.2:
                return 1.0
        return 0.0

class CumulativeDelta:
    """Tracks buying/selling pressure with Kalman filter."""
    def __init__(self):
        self.kf = KalmanFilter(dim_x=2, dim_z=1)
        self.kf.x = np.array([0., 0.])      # position, velocity
        self.kf.F = np.array([[1., 1.], [0., 1.]])
        self.kf.H = np.array([[1., 0.]])
        self.kf.P *= 1000
        self.kf.R = 5
        self.kf.Q = np.array([[0.1, 0.1], [0.1, 0.1]])
        self.delta_history = []
        self.price_accel = 0.0

    def update(self, tick_df):
        if tick_df.empty or len(tick_df) < 2:
            return 0.0
        prices = _tick_price_series(tick_df)
        if len(prices) < 2:
            return 0.0
        last_price = prices.iloc[-1]
        prev_price = prices.iloc[-2]
        vol = 0.0
        if "volume" in tick_df.columns:
            vol = float(tick_df["volume"].iloc[-1])
        elif "volume_real" in tick_df.columns:
            vol = float(tick_df["volume_real"].iloc[-1])

        # Simple delta: if price up, buy volume; if down, sell volume
        if last_price > prev_price:
            delta = vol
        elif last_price < prev_price:
            delta = -vol
        else:
            delta = 0.0

        self.kf.predict()
        self.kf.update(self.kf.x[0] + delta)  # simple cumulative approx
        vel = self.kf.x[1]
        # Exhaustion: price accelerating but delta velocity opposite
        # We need price acceleration from somewhere; use simple return difference
        if len(prices) > 10:
            ret = (prices.iloc[-1] / prices.iloc[-10] - 1) * 100
            if ret > 0.1 and vel < -0.1:
                return 1.0
            if ret < -0.1 and vel > 0.1:
                return 1.0
        return min(1.0, abs(vel) * 0.1)

class MicrostructureDetector:
    """Rule-based detection of iceberg orders and stop hunts."""
    def __init__(self):
        pass

    def detect(self, tick_df):
        if tick_df.empty or len(tick_df) < 20:
            return 0.0
        prices = _tick_price_series(tick_df)
        if len(prices) < 20:
            return 0.0
        # Iceberg: several large ticks at same price followed by lull
        if "volume" in tick_df.columns:
            volumes = tick_df["volume"].reindex(prices.index)
        elif "volume_real" in tick_df.columns:
            volumes = tick_df["volume_real"].reindex(prices.index)
        else:
            volumes = pd.Series(np.ones(len(prices)), index=prices.index)
        prices = prices.values[-30:]
        volumes = volumes.fillna(0.0).values[-30:]
        unique, counts = np.unique(prices, return_counts=True)
        # Find price level with repeated ticks and high volume
        for price, cnt in zip(unique, counts):
            if cnt >= 3:
                mask = prices == price
                avg_vol = volumes[mask].mean()
                if avg_vol > np.median(volumes) * 2:
                    return 1.0  # iceberg candidate

        # Stop hunt: price spikes beyond recent range then reverses
        recent_high = prices[-20:-1].max()
        recent_low = prices[-20:-1].min()
        last_price = prices[-1]
        if last_price > recent_high * 1.001:  # breakout above high
            # Check if next few ticks reverse (can't look ahead, so we'll use current tick as alert)
            # Instead, we can see if price is already reversing in the last few ticks
            if len(tick_df) >= 5:
                if prices[-1] < prices[-2] and prices[-2] > recent_high:
                    return 1.0
        if last_price < recent_low * 0.999:
            if len(tick_df) >= 5:
                if prices[-1] > prices[-2] and prices[-2] < recent_low:
                    return 1.0
        return 0.0

class SentimentProxy:
    """Fear/greed from realized volatility spike."""
    def __init__(self, window=20):
        self.vol_history = []
        self.window = window

    def compute(self, tick_df):
        if tick_df.empty or len(tick_df) < 10:
            return 0.0
        # Realized volatility from 1-minute returns (approximated by tick returns)
        prices = _tick_price_series(tick_df)
        returns = prices.pct_change().dropna()
        if len(returns) < 5:
            return 0.0
        vol = returns.std() * np.sqrt(252 * 24 * 60)  # annualized roughly
        self.vol_history.append(vol)
        if len(self.vol_history) > self.window:
            self.vol_history.pop(0)
        if len(self.vol_history) >= 5:
            recent = np.mean(self.vol_history[-3:])
            older = np.mean(self.vol_history[:-3]) if len(self.vol_history) > 3 else recent
            if older > 0 and (recent - older) / older > 0.5:
                return 1.0  # fear spike
        return 0.0

class QuantumOscillator:
    """Simplified quantum-inspired probability current using KDE of recent returns."""
    def __init__(self, window=50):
        self.window = window
        self.returns = []

    def compute(self, tick_df):
        if tick_df.empty or len(tick_df) < self.window:
            return 0.0
        # Use last N price changes
        prices = _tick_price_series(tick_df).values[-self.window:]
        rets = np.diff(prices) / prices[:-1]
        if len(rets) < 10:
            return 0.0
        # Kernel density of returns
        try:
            kde = gaussian_kde(rets)
            xgrid = np.linspace(-0.01, 0.01, 100)
            pdf = kde(xgrid)
            # Compute "probability current" as skew of distribution
            mean = np.sum(xgrid * pdf) / np.sum(pdf)
            std = rets.std()
            if std <= 0:
                return 0.0
            skew = np.sum(((xgrid - mean) / std) ** 3 * pdf) / np.sum(pdf)
            # Positive skew = more probability of positive returns (bullish)
            if abs(skew) > 0.5:
                return min(1.0, abs(skew))
        except:
            pass
        return 0.0

class FusionEngine:
    """Weighted average of component scores."""
    def __init__(self, weights=None):
        self.weights = weights or {
            'topography': 1.0,
            'wavelet': 1.2,
            'entropy': 1.0,
            'delta': 1.1,
            'microstructure': 1.3,
            'sentiment': 0.8,
            'quantum': 1.5
        }
        self.scores = {}

    def update(self, name, score):
        self.scores[name] = score

    def fuse(self):
        total_weight = sum(self.weights.get(k, 1.0) for k in self.scores)
        if total_weight == 0:
            return 0.0
        composite = sum(self.scores.get(k, 0.0) * self.weights.get(k, 1.0) for k in self.scores) / total_weight
        return composite

    def reset(self):
        self.scores = {}

class SniperAlert:
    """Generate grid levels and send notifications."""
    def __init__(self, cooldown_seconds: int = 60):
        self.cooldown_seconds = cooldown_seconds
        self.last_alert_time = {}

    def calculate_grid(self, symbol, current_price, direction, atr, digits):
        # Simple ATR-based grid if no orderbook
        if direction > 0:  # long
            entry1 = current_price - atr * 0.5
            entry2 = current_price - atr * 1.0
            tp = current_price + atr * 1.5
            sl = current_price - atr * 2.0
        else:
            entry1 = current_price + atr * 0.5
            entry2 = current_price + atr * 1.0
            tp = current_price - atr * 1.5
            sl = current_price + atr * 2.0
        return {
            'direction': 'LONG' if direction > 0 else 'SHORT',
            'entry1': round(entry1, digits),
            'entry2': round(entry2, digits),
            'take_profit': round(tp, digits),
            'stop_loss': round(sl, digits)
        }

    def send(self, symbol, composite, grid, components):
        now = datetime.now()
        if symbol in self.last_alert_time:
            if (now - self.last_alert_time[symbol]).total_seconds() < self.cooldown_seconds:
                return  # rate limit
        self.last_alert_time[symbol] = now

        message = (f"ALPHA HUNTER ALERT\n"
                   f"Symbol: {symbol}\n"
                   f"Time: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                   f"Composite Score: {composite:.2f}\n"
                   f"Direction: {grid['direction']}\n"
                   f"Entry 1: {grid['entry1']}\n"
                   f"Entry 2: {grid['entry2']}\n"
                   f"TP: {grid['take_profit']}\n"
                   f"SL: {grid['stop_loss']}\n"
                   f"Components:\n")
        for name, score in components.items():
            message += f"  {name}: {score:.2f}\n"

        logger.info(message)

# ================== MAIN SCANNER ==================
class AlphaHunterScanner:
    def __init__(self):
        self.client = MT5Client(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH)
        self.topography = OrderBookTopography()
        self.wavelet = WaveletPhaseCoherence()
        self.entropy = {}
        self.delta = {}
        self.micro = MicrostructureDetector()
        self.sentiment = {}
        self.quantum = {}
        self.fusion = FusionEngine()
        self.alert = SniperAlert()

    def process_symbol(self, symbol):
        logger.debug(f"Processing {symbol}")
        self.fusion.reset()
        # Fetch data
        ticks = self.client.get_ticks(symbol, lookback_seconds=300)
        orderbook = self.client.get_orderbook(symbol)
        rates_m1 = self.client.get_rates(symbol, mt5.TIMEFRAME_M1, 500)
        rates_m5 = self.client.get_rates(symbol, mt5.TIMEFRAME_M5, 100)
        rates_m15 = self.client.get_rates(symbol, mt5.TIMEFRAME_M15, 50)

        if ticks.empty:
            return

        prices = _tick_price_series(ticks)
        if prices.empty:
            return
        current_price = prices.iloc[-1]
        sym_info = self.client.symbol_info(symbol)
        digits = sym_info.digits if sym_info is not None else 5

        # 1. Order Book Topography
        score_topo = self.topography.compute(orderbook, current_price)
        self.fusion.update('topography', score_topo)

        # 2. Wavelet Phase Coherence
        if not rates_m1.empty and not rates_m5.empty and not rates_m15.empty:
            score_wave = self.wavelet.compute(
                rates_m1['close'].values,
                rates_m5['close'].values,
                rates_m15['close'].values
            )
        else:
            score_wave = 0.0
        self.fusion.update('wavelet', score_wave)

        # 3. Liquidity Entropy
        entropy = self.entropy.setdefault(symbol, LiquidityEntropy())
        score_entropy = entropy.compute(orderbook)
        self.fusion.update('entropy', score_entropy)

        # 4. Cumulative Delta
        delta = self.delta.setdefault(symbol, CumulativeDelta())
        score_delta = delta.update(ticks)
        self.fusion.update('delta', score_delta)

        # 5. Microstructure (rule-based)
        score_micro = self.micro.detect(ticks)
        self.fusion.update('microstructure', score_micro)

        # 6. Sentiment Proxy
        sentiment = self.sentiment.setdefault(symbol, SentimentProxy())
        score_sent = sentiment.compute(ticks)
        self.fusion.update('sentiment', score_sent)

        # 7. Quantum Oscillator
        quantum = self.quantum.setdefault(symbol, QuantumOscillator())
        score_quantum = quantum.compute(ticks)
        self.fusion.update('quantum', score_quantum)

        # Fuse
        composite = self.fusion.fuse()

        if composite >= FUSION_THRESHOLD:
            # Determine direction: positive from delta/quantum
            direction = 1 if score_delta > 0.5 or score_quantum > 0.5 else -1
            atr = 0.0
            if not rates_m5.empty:
                high = rates_m5['high'].values
                low = rates_m5['low'].values
                close = rates_m5['close'].values
                tr = np.maximum(
                    high[1:] - low[1:],
                    np.maximum(
                        np.abs(high[1:] - close[:-1]),
                        np.abs(low[1:] - close[:-1]),
                    ),
                )
                atr = float(np.mean(tr[-50:])) if len(tr) >= 50 else float(np.mean(tr))
            if atr <= 0:
                atr = max(current_price * 0.001, 0.0001)
            grid = self.alert.calculate_grid(symbol, current_price, direction, atr, digits)
            self.alert.send(symbol, composite, grid, self.fusion.scores)

        return composite

    def run(self):
        if not self.client.connect():
            logger.error("Failed to connect to MT5")
            return

        # Ensure symbols are visible
        resolved = []
        for symbol in SYMBOLS:
            real = _resolve_symbol(symbol)
            if not mt5.symbol_select(real, True):
                logger.warning(f"Failed to select {real}")
                continue
            mt5.market_book_add(real)
            if real != symbol:
                logger.info(f"Resolved {symbol} -> {real}")
            resolved.append(real)

        if not resolved:
            logger.error("No symbols available after resolution")
            self.client.disconnect()
            return

        logger.info(f"Starting scanner loop (interval={SCAN_INTERVAL}s)")
        try:
            while True:
                loop_start = time.time()
                for symbol in resolved:
                    try:
                        self.process_symbol(symbol)
                    except Exception as e:
                        logger.error(f"Error on {symbol}: {e}")
                elapsed = time.time() - loop_start
                if elapsed < SCAN_INTERVAL:
                    time.sleep(SCAN_INTERVAL - elapsed)
        except KeyboardInterrupt:
            logger.info("Stopped by user")
        finally:
            for symbol in resolved:
                try:
                    mt5.market_book_release(symbol)
                except Exception:
                    pass
            self.client.disconnect()

if __name__ == "__main__":
    scanner = AlphaHunterScanner()
    scanner.run()