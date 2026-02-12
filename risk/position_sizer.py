"""
===============================================================================
  Position Sizer — compute exact lot size for each trade
===============================================================================
  Combines:  Account equity × Risk % × Kelly fraction
  Divided by: Stop-loss distance × Pip value
  To produce the exact volume in lots.
===============================================================================
"""

from __future__ import annotations

from typing import Optional

import MetaTrader5 as mt5

import config as cfg
from risk.kelly import kelly_from_confidence
from core.mt5_connector import MT5Connector
from utils.logger import get_logger

log = get_logger("position_sizer")


def compute_position_size(
    mt5_conn: MT5Connector,
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    confidence: float,
    risk_reward_ratio: float,
    trading_capital: float | None = None,
    adjusted_risk_pct: float | None = None,
) -> float:
    """
    Compute the optimal lot size.

    Steps:
    1. Determine risk fraction via Kelly criterion.
    2. Calculate dollar amount at risk.
    3. Determine the SL distance in price.
    4. Use MT5's margin/profit calculators to find the right lot size.
    5. Round down to the symbol's volume_step.
    """
    # Use live equity if available, otherwise fall back to config
    acc_info = mt5_conn.account_info()
    live_equity = acc_info.get("equity", 0)
    trading_capital = trading_capital or (live_equity if live_equity > 0 else cfg.TRADING_CAPITAL)

    # ── Step 1: Kelly-adjusted risk percentage ───────────────────────────
    kelly_frac = kelly_from_confidence(confidence, risk_reward_ratio)
    if kelly_frac <= 0:
        log.info(f"{symbol}: Kelly says no edge — skip")
        return 0.0

    # Base risk = risk manager's adjusted % (accounts for daily/weekly losses),
    # but Kelly can further reduce it
    max_risk = adjusted_risk_pct if adjusted_risk_pct is not None else cfg.MAX_RISK_PER_TRADE_PCT
    risk_pct = min(kelly_frac * 100, max_risk)

    # ── Step 2: Dollar risk ──────────────────────────────────────────────
    dollar_risk = trading_capital * (risk_pct / 100)

    # ── Step 3: SL distance ──────────────────────────────────────────────
    sl_distance = abs(entry_price - stop_loss)
    if sl_distance == 0:
        return 0.0

    # ── Step 4: Calculate lot size using MT5's profit calculator ─────────
    sym_info = mt5_conn.symbol_info(symbol)
    if sym_info is None:
        log.warning(f"Cannot get symbol info for {symbol}")
        return 0.0

    vol_min = sym_info.get("volume_min", 0.01)
    vol_max = sym_info.get("volume_max", 100.0)
    vol_step = sym_info.get("volume_step", 0.01)
    contract_size = sym_info.get("trade_contract_size", 100000)
    point = sym_info.get("point", 0.00001)

    if point == 0:
        return 0.0

    # Method: Use order_calc_profit to find how much 1 lot earns per sl_distance
    action = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

    # Profit for 1 lot if price moves by sl_distance
    if direction == "BUY":
        test_profit = mt5_conn.calc_profit(
            action, symbol, 1.0, entry_price, entry_price + sl_distance
        )
    else:
        test_profit = mt5_conn.calc_profit(
            action, symbol, 1.0, entry_price, entry_price - sl_distance
        )

    if test_profit is None or test_profit <= 0:
        # Fallback: estimate from contract size and tick value
        tick_value = sym_info.get("trade_tick_value", 1.0)
        tick_size = sym_info.get("trade_tick_size", point)
        if tick_size > 0:
            profit_per_lot = (sl_distance / tick_size) * tick_value
        else:
            profit_per_lot = sl_distance * contract_size
        if profit_per_lot <= 0:
            return 0.0
        test_profit = profit_per_lot

    # Lots = dollar_risk / profit_per_lot_per_sl_distance
    raw_lots = dollar_risk / test_profit

    # ── Step 5: Round to volume step ─────────────────────────────────────
    if vol_step > 0:
        lots = max(vol_min, int(raw_lots / vol_step) * vol_step)
    else:
        lots = max(vol_min, round(raw_lots, 2))

    lots = min(lots, vol_max)

    # ── Guard: if vol_min exceeds the intended risk, don't trade ─────────
    # On small accounts or wide-SL instruments (gold, indices), the
    # minimum lot may represent 2-5x the risk budget.  Never over-risk.
    if lots == vol_min and raw_lots < vol_min * 0.66:
        actual_dollar_risk = lots * test_profit
        log.warning(
            f"{symbol}: vol_min={vol_min} exceeds risk budget "
            f"(${actual_dollar_risk:.2f} vs target ${dollar_risk:.2f}) — skip"
        )
        return 0.0

    # ── Margin check ─────────────────────────────────────────────────────
    margin = mt5_conn.calc_margin(action, symbol, lots, entry_price)
    free_margin = acc_info.get("margin_free", 0)  # reuse same snapshot
    if margin is not None and free_margin > 0:
        if margin > free_margin * 0.8:  # don't use more than 80% of free margin
            # Scale down
            safe_lots = lots * (free_margin * 0.5 / margin)
            if vol_step > 0:
                safe_lots = max(vol_min, int(safe_lots / vol_step) * vol_step)
            else:
                safe_lots = max(vol_min, round(safe_lots, 2))
            lots = min(safe_lots, vol_max)
            log.warning(
                f"{symbol}: margin constraint — reduced to {lots:.2f} lots"
            )

    log.info(
        f"{symbol}: size={lots:.2f} lots  "
        f"risk={risk_pct:.2f}% (${dollar_risk:.2f})  "
        f"kelly_frac={kelly_frac:.4f}  SL_dist={sl_distance:.5f}"
    )
    return round(lots, 2)
