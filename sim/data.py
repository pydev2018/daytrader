from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from brokers.mt5 import MT5Broker
from sim.sim_types import SymbolSpec


def _load_cached_history_with_fallback(
    broker: MT5Broker,
    symbol: str,
    timeframe: str,
) -> pd.DataFrame | None:
    for count in (300000, 200000, 100000, 50000, 20000, 10000, 5000):
        df = broker.get_rates(symbol, timeframe, count)
        if df is not None and not df.empty:
            return df
    return None


def load_mt5_history(
    symbols: list[str],
    start: datetime,
    end: datetime,
    timeframe: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, SymbolSpec]]:
    broker = MT5Broker()
    if not broker.connect():
        raise RuntimeError("Could not connect MT5 for backtest data")

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    req_start_ts = pd.Timestamp(start).tz_convert("UTC")
    req_end_ts = pd.Timestamp(end).tz_convert("UTC")
    req_start_epoch = int(req_start_ts.timestamp())
    req_end_epoch = int(req_end_ts.timestamp())

    data: dict[str, pd.DataFrame] = {}
    specs: dict[str, SymbolSpec] = {}

    for symbol in symbols:
        resolved = broker.resolve_symbol(symbol)
        if not resolved:
            print(f"[BACKTEST] Symbol not found in MT5: {symbol}")
            continue
        if not broker.select_symbol(resolved):
            print(f"[BACKTEST] Could not select symbol: {resolved} (requested={symbol})")
            continue

        rates = broker.get_rates_range(resolved, timeframe, start, end)
        if rates is None or rates.empty:
            cached = _load_cached_history_with_fallback(broker, resolved, timeframe)
            if cached is not None and not cached.empty:
                cached = cached.sort_values("time").reset_index(drop=True)
                cached["time"] = pd.to_datetime(cached["time"], utc=True)
                cached_epoch = (cached["time"].astype("int64") // 10**9).astype("int64")

                available_start = cached["time"].min()
                available_end = cached["time"].max()
                avail_start_epoch = int(available_start.timestamp())
                avail_end_epoch = int(available_end.timestamp())

                clip_start_epoch = max(req_start_epoch, avail_start_epoch)
                clip_end_epoch = min(req_end_epoch, avail_end_epoch)
                clip_start = pd.to_datetime(clip_start_epoch, unit="s", utc=True)
                clip_end = pd.to_datetime(clip_end_epoch, unit="s", utc=True)

                if clip_start_epoch <= clip_end_epoch:
                    # Robust slice by sorted timestamp index (safer than boolean masks
                    # on some MT5/pandas builds that yielded false-empty results).
                    time_values = cached["time"].values
                    left = time_values.searchsorted(clip_start.to_datetime64(), side="left")
                    right = time_values.searchsorted(clip_end.to_datetime64(), side="right")
                    in_window = cached.iloc[left:right]
                else:
                    in_window = cached.iloc[0:0]

                if not in_window.empty:
                    rates = in_window
                    if clip_start != req_start_ts or clip_end != req_end_ts:
                        print(
                            f"[BACKTEST] {symbol} clipped to available range: "
                            f"{clip_start.isoformat()} -> {clip_end.isoformat()}"
                        )
                else:
                    print(
                        f"[BACKTEST] Overlap expected but zero bars after filter for {symbol} "
                        f"(resolved={resolved}). overlap={clip_start.isoformat()} -> {clip_end.isoformat()}"
                    )

        info = broker.symbol_info(resolved)
        if rates is None or rates.empty or not info:
            cached = _load_cached_history_with_fallback(broker, resolved, timeframe)
            if cached is not None and not cached.empty:
                cached["time"] = pd.to_datetime(cached["time"], utc=True)
                first = cached["time"].min()
                last = cached["time"].max()
                print(
                    f"[BACKTEST] No bars in requested window for {symbol} (resolved={resolved}). "
                    f"Available cached range: {first.isoformat()} -> {last.isoformat()}"
                )
            else:
                print(
                    f"[BACKTEST] No history for {symbol} (resolved={resolved}) "
                    f"from {start.isoformat()} to {end.isoformat()}"
                )
            continue

        rates = rates.sort_values("time").reset_index(drop=True)
        if "spread" not in rates.columns:
            rates["spread"] = 0

        point = float(info.get("point", 0.00001))
        pip_size = broker.pip_size(info)
        contract_size = float(info.get("trade_contract_size", 100000.0) or 100000.0)
        tick_value = float(info.get("trade_tick_value", 0.0) or 0.0)
        tick_size = float(info.get("trade_tick_size", point) or point)
        if tick_value > 0 and tick_size > 0:
            pip_value = tick_value * (pip_size / tick_size)
        else:
            pip_value = contract_size * pip_size

        specs[resolved] = SymbolSpec(
            point=point,
            pip_size=pip_size,
            contract_size=contract_size,
            pip_value_per_lot=float(pip_value),
        )
        data[resolved] = rates

        if resolved != symbol:
            print(f"[BACKTEST] {symbol} resolved to {resolved}")

    broker.disconnect()
    return data, specs
