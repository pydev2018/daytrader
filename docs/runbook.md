# Runbook (Operations)

## Deployment (Windows)
1. **Install MT5 terminal** and verify you can log in manually.
2. **Install Python dependencies**:
   - `pip install -r requirements.txt`
3. **Configure environment**:
   - Copy `.env.template` → `.env` and set:
     - `MT5_PATH`, `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (if not using current MT5 session).
     - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` for alerts.
     - `OPENAI_API_KEY` if chart analysis is enabled.
4. **Verify connection**:
   - `python main.py --status` (prints account and symbol status).

## Running the System
- **Live trading**: `python main.py`
- **Scan-only**: `python main.py --scan-only`
- **Status**: `python main.py --status`
- **Conda wrapper**: `run.bat main.py`
  
Backtesting (Sniper):
- `python backtest_sniper.py --start YYYY-MM-DD --end YYYY-MM-DD --symbols EURUSD,GBPUSD`

## Continuous Operation
No service/scheduler configuration exists in the repo. Best-practice options:
- **Windows Task Scheduler**: run `python main.py` at startup and restart on failure.
- **NSSM (service wrapper)**: wrap `run.bat main.py` into a Windows service.

Document any chosen approach in a local ops SOP to standardize restarts and logs.

## Logs and Data Locations
- Logs: `logs/<logger>.log` (rotating file handler) (`utils/logger.py`).
- Trade journal: `data/trade_journal.json` (`risk/risk_manager.py`).
- Risk state: `data/risk_state.json` (persisted daily/weekly state) (`risk/risk_manager.py`).
- News cache: `data/news_cache.json` (`core/news_aggregator.py`).
- Chart analysis artifacts: `logs/chart_analysis/<SYMBOL>/<timestamp>/` (`core/chart_analyst.py`).

## Observability
- **Logs**: structured UTC logs to console and rotating files (`utils/logger.py`).
- **Alerts**: Telegram notifications for trade opens/closes and safety events (`alerts/telegram.py`).
- **Metrics**: no metrics export or dashboards are implemented; consider adding Prometheus/CSV metrics if needed.

## Safe Shutdown / Restart
`main.py` installs a signal handler and exits the loop on `SIGINT`/`SIGTERM`. Use Ctrl+C in the console or stop the service cleanly to allow `WolfEngine` to finish its cycle and disconnect MT5.

## Incident Playbooks
### MT5 Disconnect / Reconnect Loop
Symptoms: repeated `MT5 connection lost — reconnecting …`.
Actions:
- Verify MT5 terminal is running and logged in.
- Confirm `MT5_PATH` and credentials in `.env`.
- Restart `main.py` if connection does not recover (`core/mt5_connector.py` retries then raises).

### Trading Halted (Daily/Weekly/Drawdown)
Symptoms: log shows `Trading halted`.
Actions:
- Review `data/risk_state.json` and logs for halt reason (`risk/risk_manager.py`).
- After investigation, a manual clear is possible via `RiskManager.clear_halt(confirm="I_ACCEPT_THE_RISK")` (no CLI wrapper exists; add one if needed).

### Orders Rejected / STOPLEVEL Errors
Symptoms: `order_check failed` or `order_send rejected`.
Actions:
- Inspect logs from `execution/trade_executor.py`.
- Verify broker minimum stop distance; executor auto-adjusts SL/TP but still may be rejected.
- Confirm symbol is tradeable and selected in MarketWatch (`core/mt5_connector.py`).

### Empty Watchlist / No Signals
Actions:
- Check `SCAN_GROUPS` and `EXCLUDE_SYMBOLS` in `config.py`.
- Verify market sessions are open (`utils/market_hours.py`).
- Check spread filter (`MAX_SPREAD_PIPS`) and event windows (`core/news_aggregator.py`).

### Chart Analysis Failing
Symptoms: chart analysis skipped or errors.
Actions:
- Verify `OPENAI_API_KEY` and `CHART_ANALYSIS_ENABLED` (`config.py`).
- System will continue with `risk_factor=1.0` if analysis fails (`core/chart_analyst.py`).

### Telegram Alerts Not Sent
Actions:
- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
- Check network access and logs (`alerts/telegram.py`).

## Security Notes
Credentials are loaded from `.env` in the repo root (`config.py`). This is plaintext by default; recommended improvements:
- Use Windows Credential Manager or environment variables injected by the service.
- Restrict filesystem permissions on `.env`.

## Repository Evidence Index
- `main.py` — runtime loop and shutdown handling.
- `core/mt5_connector.py` — connection and reconnect logic.
- `execution/trade_executor.py` — order validation and error handling.
- `risk/risk_manager.py` — halts, persistence, and trade journal.
- `utils/logger.py` — log paths and rotation.
