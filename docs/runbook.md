# Runbook — OCO Breakout Trading System

## Prerequisites
- **OS**: Windows (MT5 only runs on Windows)
- **Python**: 3.10+ (tested on 3.11)
- **Conda env**: `tradebot`
- **MT5 Terminal**: installed and logged in

## Environment Setup

```bash
conda activate tradebot
pip install -r requirements.txt
```

## Configuration

1. Copy `.env.template` to `.env`
2. Fill in MT5 credentials and trading parameters
3. Key parameters to review before first run:

| Parameter | Purpose | Conservative Default |
|---|---|---|
| `OCO_SYMBOLS` | Symbols to trade | `EURUSD` |
| `OCO_TIMEFRAME` | Breakout source bars | `M5` |
| `OCO_BREAKOUT_LOOKBACK` | Breakout window size | `80` |
| `OCO_ARM_TTL_SECONDS` | Pending-arm expiry | `1800` |
| `BASE_ORDER_SIZE_LOTS` | Base lot size | `0.01` |
| `OCO_SL_SPACING_MULT` | Initial SL distance multiple | `2.0` |
| `OCO_TRAIL_ACTIVATE_R` | Trailing activation threshold | `0.9` |
| `OCO_TIME_STOP_SECONDS` | Max hold time per OCO leg | `7200` |
| `MAX_INVENTORY_LOTS` | Max inventory cap | `1.0` |
| `MAX_DRAWDOWN_PCT` | Kill-switch drawdown | `15.0` |
| `DAILY_LOSS_LIMIT_PCT` | Daily loss halt | `3.0` |
| `MAX_LEVERAGE` | Leverage cap | `5.0` |

## Commands

### Live Trading
```bash
conda activate tradebot
python main.py
```

### Account Status Check
```bash
python main.py --status
```

### Run Tests
```bash
conda activate tradebot
python -m pytest tests/ -v
```

## Operational States

| State | Meaning | Action |
|---|---|---|
| **IDLE** | No active arm/position | Await next arm cycle |
| **ARMED** | OCO stop entries active | Waiting for breakout trigger |
| **POSITION** | One OCO leg triggered | Opposite pending cancelled; trailing/time-stop active |
| **COOLDOWN** | Post-close/risk pause | No new arm until cooldown ends |
| **HALTED** | Risk limit breached | Orders cancelled; unwind attempted |

## Risk Controls

### Auto-clearing halts
These halts clear automatically when the condition resolves:
- `inventory_limit` → clears when inventory drops below `MAX_INVENTORY_LOTS`
- `leverage_limit` → clears when leverage drops below `MAX_LEVERAGE`
- `margin_level` → clears when margin level rises above 200%
- `daily_loss_limit` → clears at start of new trading day (UTC)
- `weekly_loss_limit` → clears at start of new trading week (Monday UTC)
- `consecutive_errors` → clears on next successful loop

### Manual-reset halts
- `max_drawdown` → **requires manual intervention** (safety by design)
  - To reset: delete `data/state/risk_state.json` or set `halted: false` in the file

## Monitoring

### Logs
- Console: real-time stdout
- File: `logs/wolf.log` (10MB rotating, 5 backups)

### Trade Journal
- Path: `data/trade_journal.jsonl`
- Format: one JSON object per line
- Events: `fill`, `order_placed`, `order_cancelled`, `safety`

### Telegram Alerts
Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` to receive:
- Trade notifications
- Risk halt alerts
- Daily summaries
- Bot start/stop status

## Troubleshooting

### "MT5 connection lost"
- Check MT5 terminal is running and logged in
- Verify `MT5_PATH` in `.env` points to correct terminal
- System retries 5 times with exponential backoff before halting

### "CONFIGURATION ERRORS" on startup
- Check `.env` for invalid parameter values
- All parameters are validated at startup with clear error messages

### System halted and won't restart
- Check `data/state/risk_state.json` for `halted: true`
- If `halt_reason` is `max_drawdown`, manual reset required (see above)
- Other halt reasons auto-clear when conditions resolve

### Orders not being placed
- Check if symbol is in `OCO_REENTRY_COOLDOWN_SECONDS`
- Check if arm TTL is expiring repeatedly due to low volatility
- Check MT5 terminal log for stop-order rejections
- Verify stops_level constraints aren't filtering stop entries
