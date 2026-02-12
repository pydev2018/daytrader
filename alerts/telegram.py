"""
===============================================================================
  TELEGRAM ALERTER — Real-time trade notifications via Telegram
===============================================================================
  Setup:
    1. Create a bot: talk to @BotFather on Telegram, get the token
    2. Get your chat ID: talk to @userinfobot
    3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
===============================================================================
"""

import time
from concurrent.futures import ThreadPoolExecutor

import requests

from utils.logger import get_logger

logger = get_logger("telegram")


class TelegramAlerter:
    """Non-blocking Telegram notifications via HTTP.

    Sends are dispatched to a single background thread so they never stall
    the main trading loop (even if Telegram is slow or unreachable).
    """

    # Minimum interval between consecutive sends (rate-limit guard)
    _MIN_SEND_INTERVAL = 0.5  # seconds

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

        if self.enabled:
            self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            # Connection-pooling session (reuses TCP/TLS)
            self._session = requests.Session()
            # Single-thread executor — serialises sends, never blocks main loop
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="telegram"
            )
            logger.info("Telegram alerts enabled (non-blocking, HTTP API)")
        else:
            self._url = ""
            self._session = None
            self._executor = None
            logger.info("Telegram alerts disabled (no token/chat_id)")

        self._last_send_time: float = 0.0

    # =========================================================================
    # INTERNAL SEND (runs on background thread)
    # =========================================================================

    def _send(self, text: str):
        """Queue a message for async delivery. Never blocks the caller."""
        if not self.enabled or self._executor is None:
            return
        self._executor.submit(self._do_send, text)

    def _do_send(self, text: str, retries: int = 1):
        """Actual HTTP send — runs on the background thread."""
        # Rate-limit: wait if we sent too recently
        elapsed = time.monotonic() - self._last_send_time
        if elapsed < self._MIN_SEND_INTERVAL:
            time.sleep(self._MIN_SEND_INTERVAL - elapsed)

        for attempt in range(1 + retries):
            try:
                resp = self._session.post(
                    self._url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                    timeout=(5, 10),  # (connect, read) — explicit tuple
                )
                self._last_send_time = time.monotonic()
                if resp.ok:
                    return
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    logger.warning(f"Telegram rate-limited, waiting {retry_after}s")
                    time.sleep(retry_after)
                    continue
                logger.warning(
                    f"Telegram send failed: {resp.status_code} {resp.text[:200]}"
                )
            except Exception as e:
                logger.warning(f"Telegram send failed: {e}")
                if attempt < retries:
                    time.sleep(2)

    # =========================================================================
    # ALERT TYPES
    # =========================================================================

    def trade_opened(
        self, symbol, direction, volume, entry_price,
        tp_price, sl_price, cycle, step, balance,
    ):
        step_label = f"Step {step}" if step > 0 else "New cycle"
        msg = (
            f"<b>TRADE OPENED</b>\n"
            f"<b>{symbol}</b> {direction}\n"
            f"Volume: {volume} lots | Entry: {entry_price}\n"
            f"TP: {tp_price} | SL: {sl_price}\n"
            f"Cycle #{cycle} — {step_label}\n"
            f"Balance: ${balance:,.2f}"
        )
        self._send(msg)

    def trade_closed(
        self, symbol, direction, result, pnl,
        close_price, balance, cycle, step,
    ):
        label = {
            "TP": "TAKE PROFIT",
            "SL": "STOP LOSS",
        }.get(result, result)
        sign = "+" if pnl >= 0 else ""
        msg = (
            f"<b>{label}</b>\n"
            f"<b>{symbol}</b> {direction}\n"
            f"Close: {close_price}\n"
            f"P/L: {sign}${pnl:,.2f}\n"
            f"Cycle #{cycle} Step {step}\n"
            f"Balance: ${balance:,.2f}"
        )
        self._send(msg)

    def cycle_complete(self, cycle, won, total_pnl, balance, steps_taken):
        label = "CYCLE WON" if won else "CYCLE LOST"
        sign = "+" if total_pnl >= 0 else ""
        msg = (
            f"<b>{label}</b> — Cycle #{cycle}\n"
            f"Steps: {steps_taken} | P/L: {sign}${total_pnl:,.2f}\n"
            f"Balance: ${balance:,.2f}"
        )
        self._send(msg)

    def safety_event(self, event_type, details, balance):
        msg = (
            f"<b>SAFETY: {event_type}</b>\n"
            f"{details}\n"
            f"Balance: ${balance:,.2f}"
        )
        self._send(msg)

    def daily_summary(
        self, balance, starting_balance, trades_today,
        wins_today, pnl_today, open_trades,
    ):
        total_return = (balance / starting_balance - 1) * 100 if starting_balance > 0 else 0
        sign = "+" if pnl_today >= 0 else ""
        msg = (
            f"<b>DAILY SUMMARY</b>\n"
            f"Balance: ${balance:,.2f} ({total_return:+.1f}%)\n"
            f"Today: {sign}${pnl_today:,.2f} ({trades_today} trades, {wins_today} wins)\n"
            f"Open positions: {open_trades}"
        )
        self._send(msg)

    def bot_status(self, status, details=""):
        msg = f"<b>BOT {status}</b>"
        if details:
            msg += f"\n{details}"
        self._send(msg)

    def custom(self, message):
        self._send(message)
