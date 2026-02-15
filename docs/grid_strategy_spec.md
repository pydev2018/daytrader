# Grid Strategy Specification

## A) EXECUTIVE SUMMARY
This system runs a **regime-aware, cost-aware grid** around a dynamic anchor. It places a ladder of buy/sell limits around a center price, captures mean-reverting oscillations, and explicitly manages inventory risk, drawdown, and operational failures. The strategy is engineered to *pause or unwind* when regimes shift (trend, volatility spike, spread blowout).

**When it trades:** range/mean-reverting regimes with stable spreads and normal volatility.  
**When it throttles:** trend or rising volatility.  
**When it stops:** spread blowouts, vol shocks, inventory or drawdown breaches, or operational failure.  
**Why it can work:** it monetizes price oscillations *if* cost floors and risk limits are respected. It is not guaranteed, and it is explicitly short convexity.

## B) STRATEGY SPEC (Precise Rules)

### B1) Observables (no lookahead)
- Bid/Ask: `b_t`, `a_t`
- Mid: `m_t = (a_t + b_t) / 2`
- Spread: `s_t = a_t - b_t`
- Log return: `r_t = ln(m_t / m_{t-1})`

### B2) Anchor (center) logic
Anchor is a time-aware EMA with half-life `T_1/2` seconds:
```
alpha(dt) = 1 - exp(-ln(2) * dt / T_1/2)
A_t = (1 - alpha) * A_{t-1} + alpha * m_t
```
Inventory-skewed center:
```
u_t = clip(I_t / I_max, -1, +1)
C_t = A_t - k_skew * u_t * Δ
```
If long (`u_t > 0`), shift center down to bias toward inventory-reducing sells.

### B3) Spacing (vol + cost aware)
EWMA volatility:
```
σ_t^2 = λ * σ_{t-1}^2 + (1-λ) * r_t^2
```
Vol move over horizon `H`:
```
σ_H = σ_t * sqrt(H / dt)
volMove_t = m_t * σ_H
```
Cost floor:
```
costFloor = spread + slippage_buffer
```
Spacing:
```
Δ = round_to_tick(max(k_sigma * volMove, k_cost * costFloor, Δ_min))
```
Edge gate:
```
Δ - costFloor >= edgeMin
```
If the edge gate fails, new entries are paused.

### B4) Rungs and order placement
Levels are indexed by `i = -N..-1, 1..N`.  
Entry price:
```
P_i = C_t + i * Δ
```
Entry side:
- `i < 0` => BUY
- `i > 0` => SELL

### B5) Order sizing (anti-martingale)
Base size `q0` (lots), then:
```
φ_buy = clip(1 - γ * u_t, φ_min, φ_max)
φ_sell = clip(1 + γ * u_t, φ_min, φ_max)
ψ(i) = 1 / (1 + η * (|i| - 1))
q_i = q0 * φ * ψ
```

### B6) Profit capture (one-rung take-profit)
When an entry fills at price `P_entry`, immediately place an exit order one spacing away:
- If BUY filled: exit = SELL at `P_entry + Δ`
- If SELL filled: exit = BUY at `P_entry - Δ`

### B7) Reset rules
Re-anchor only when center drift exceeds:
```
|C_t - C*| >= k_reset * Δ
```
and minimum time since last reset has passed. On reset, ENTRY prices are recalculated; EXIT orders remain at their original prices.

### B8) State machine
States:
- **ACTIVE**: grid entries enabled
- **CAUTION**: size reduced (trend warning)
- **PAUSED**: no new entries (vol/spread shock)
- **HALTED**: risk halt; cancel orders and unwind

## C) RISK & SAFETY LAYER

### C1) Inventory & exposure limits
- Max inventory lots: `|I_t| <= I_max`
- Max notional: `|I_t| * contract_size * price <= equity * max_notional_mult`
- Max leverage: `notional / equity <= L_max`

### C2) Drawdown and loss limits
- Daily loss limit (equity-based) → HALT
- Weekly loss limit → reduce sizing
- Max drawdown (peak-to-trough) → HALT + unwind

### C3) Circuit breakers
Trigger HALT when:
- Drawdown threshold breached
- Margin level too low
- Inventory or leverage exceeds limits

Action:
- Cancel pending orders
- Market unwind to flat (if possible)

## D) EXECUTION DESIGN
- **Order type**: limit orders for grid entries/exits; market only for unwind.
- **Re-quote cadence**: orders updated at `ORDER_REFRESH_SECONDS`, not every tick.
- **Idempotency**: order comments encode grid/rung/state for reconciliation.
- **Restart recovery**: load persisted grid state; reconcile with broker state.

## E) PARAMETER TABLE
| Parameter | Meaning | Suggested Range | Notes |
| --- | --- | --- | --- |
| GRID_LEVELS | rungs per side | 3–12 | more levels => more inventory risk |
| ANCHOR_HALFLIFE_SECONDS | EMA half-life | 30–1800 | lower => faster re-center |
| VOL_EWMA_LAMBDA | vol decay | 0.90–0.99 | higher => smoother |
| VOL_HORIZON_SECONDS | spacing horizon | 30–600 | higher => wider spacing |
| GRID_SPACING_K_SIGMA | vol spacing multiplier | 0.5–2.5 | controls fill frequency |
| GRID_SPACING_K_COST | cost floor multiplier | 1.2–2.5 | >1 to survive costs |
| SLIPPAGE_BUFFER_TICKS | slippage buffer | 0–3 | increase in fast markets |
| BASE_ORDER_SIZE_LOTS | base size | instrument-dependent | start small |
| INVENTORY_SKEW_GAMMA | size skew | 0.2–1.0 | higher => faster de-risk |
| SIZE_TAPER_ETA | depth taper | 0–0.5 | reduces outer sizes |
| GRID_RESET_K | reset threshold | 1–4 | higher = fewer resets |
| MAX_INVENTORY_LOTS | inventory cap | 0.5–5.0 | tail risk control |
| MAX_DRAWDOWN_PCT | max DD | 5–25 | hard kill-switch |

## F) BACKTEST + VALIDATION PLAN
1. **Bid/ask backtest** using `backtest/grid_engine.py` (no mid-only).
2. **Walk-forward** with rolling windows; optimize only 2–3 parameters.
3. **Stress tests**:
   - Trend torture (one-way move across multiple rungs)
   - Volatility spikes (spread blowouts)
   - Slippage shocks and delayed fills
4. **Metrics**:
   - CAGR (if meaningful), max drawdown, Calmar
   - CVaR / expected shortfall
   - Turnover, fee-to-gross ratio, fill ratio

## G) PSEUDOCODE
```python
def on_tick(symbol):
    mid, spread = get_mid_spread(symbol)
    anchor = ema_anchor.update(mid)
    sigma = vol_ewma.update(mid)
    spacing, cost_floor = compute_spacing(mid, spread, sigma)
    center = anchor - k_skew * inventory_ratio * spacing

    regime = classify_regime(trend_z, vol_ratio, spread_ratio)
    risk = risk_manager.check_limits(inventory, notional)
    if risk.halted:
        cancel_all()
        unwind()
        return

    if reset_needed(center, spacing):
        reset_grid(center, spacing)

    fills = detect_fills()
    for fill in fills:
        toggle_rung_state(fill)

    orders = build_desired_orders(rungs, allow_entries=regime.active)
    sync_orders(orders)
```

## H) WHAT CAN GO WRONG (Top 15)
1. Trend regime breaks mean reversion → inventory blowup.  
2. Volatility spike widens spread → negative edge.  
3. Gap through multiple rungs → exits slip.  
4. Over-tight grids → fee bleed.  
5. Frequent resets → cancel/replace churn.  
6. Inventory cap hit → forced unwind in bad conditions.  
7. Partial fills / rejects → rung state desync.  
8. MT5 disconnect → stale orders remain live.  
9. Slippage worse than assumed.  
10. Wrong symbol precision/tick size → invalid orders.  
11. Parameter overfit → unstable OOS performance.  
12. Liquidity droughts around rollover/news.  
13. Broker order limits exceeded.  
14. Operational error (wrong account/symbol).  
15. Regime classifier too slow/fast.

## I) NEXT QUESTIONS
1. Confirm target symbols and typical spreads for each.  
2. Confirm account leverage limits and max drawdown tolerance.  
3. Confirm if overnight holding is allowed or if you want daily flattening.  
4. Provide tick size/min lot constraints for each symbol.  
5. Confirm if you want long-only or neutral (both sides) grids.
