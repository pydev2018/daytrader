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

import logging
import requests

logger = logging.getLogger("telegram")


class TelegramAlerter:
    """Non-blocking Telegram notifications via HTTP. Fails silently if not configured."""

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

        if self.enabled:
            self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            logger.info("Telegram alerts enabled (HTTP API)")
        else:
            self._url = ""
            logger.info("Telegram alerts disabled (no token/chat_id)")

    # =========================================================================
    # INTERNAL SEND
    # =========================================================================

    def _send(self, text: str):
        """Send a message via Telegram Bot HTTP API. Simple, no async."""
        if not self.enabled:
            return
        try:
            resp = requests.post(
                self._url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            if not resp.ok:
                logger.warning(f"Telegram send failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

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
