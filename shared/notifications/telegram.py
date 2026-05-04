"""Telegram bot notification system.

Sends trading signals, health alerts, and performance updates via Telegram.
Requires a bot token and chat ID (set via environment variables or config).

Setup:
  1. Create bot via @BotFather → get BOT_TOKEN
  2. Send /start to your bot → get your CHAT_ID
  3. Set env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  4. Or pass directly: TelegramNotifier(token=..., chat_id=...)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


@dataclass
class AlertLevel:
    INFO = "ℹ️"
    WARNING = "⚠️"
    CRITICAL = "🚨"
    SIGNAL = "📊"
    PROFIT = "💰"
    LOSS = "📉"


class TelegramNotifier:
    """Send notifications via Telegram bot API."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._enabled = bool(self.token and self.chat_id)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Send a message. Returns True if successful."""
        if not self._enabled:
            logger.debug("Telegram not configured, skipping: %s", message[:80])
            return False

        url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }).encode()

        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            return result.get("ok", False)
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    # ----- Convenience methods -----

    def signal_alert(
        self,
        positions: dict[str, float],
        prices: dict[str, float] | None = None,
    ) -> bool:
        """Send trading signal update."""
        lines = [f"{AlertLevel.SIGNAL} <b>Signal Update</b>"]
        lines.append(f"<i>{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}</i>\n")

        for sym, pos in sorted(positions.items()):
            direction = "LONG" if pos > 0 else "SHORT" if pos < 0 else "FLAT"
            price_str = f" @ ${prices[sym]:,.2f}" if prices and sym in prices else ""
            lines.append(f"  {sym}: <b>{direction}</b> {pos:+.3f}{price_str}")

        net = sum(positions.values())
        lines.append(f"\nNet exposure: {net:+.3f}")
        return self.send("\n".join(lines))

    def health_alert(
        self,
        alpha_name: str,
        status: str,
        sharpe: float,
        message: str = "",
    ) -> bool:
        """Send alpha health alert."""
        level = AlertLevel.CRITICAL if status == "CRITICAL" else AlertLevel.WARNING
        lines = [
            f"{level} <b>Alpha Health: {alpha_name}</b>",
            f"Status: <b>{status}</b>",
            f"Rolling Sharpe: {sharpe:+.3f}",
        ]
        if message:
            lines.append(f"Detail: {message}")
        return self.send("\n".join(lines))

    def pnl_alert(
        self,
        daily_pnl: float,
        total_pnl: float,
        equity: float,
        drawdown: float,
    ) -> bool:
        """Send daily PnL summary."""
        level = AlertLevel.PROFIT if daily_pnl >= 0 else AlertLevel.LOSS
        lines = [
            f"{level} <b>Daily PnL Report</b>",
            f"<i>{datetime.now(timezone.utc):%Y-%m-%d UTC}</i>\n",
            f"Today: <b>{daily_pnl:+.2f}%</b>",
            f"Total: {total_pnl:+.2f}%",
            f"Equity: ${equity:,.2f}",
            f"Drawdown: {drawdown:.1f}%",
        ]

        if drawdown > 10:
            lines.append(f"\n{AlertLevel.CRITICAL} DD > 10% — monitor closely")

        return self.send("\n".join(lines))

    def execution_alert(
        self,
        orders_filled: int,
        orders_failed: int,
        total_notional: float,
    ) -> bool:
        """Send execution summary."""
        lines = [
            f"{AlertLevel.INFO} <b>Execution Summary</b>",
            f"Filled: {orders_filled}",
            f"Failed: {orders_failed}",
            f"Total Notional: ${total_notional:,.0f}",
        ]
        if orders_failed > 0:
            lines[0] = f"{AlertLevel.WARNING} <b>Execution Summary</b>"
        return self.send("\n".join(lines))

    def system_alert(self, message: str, level: str = "INFO") -> bool:
        """Send generic system alert."""
        icon = getattr(AlertLevel, level, AlertLevel.INFO)
        return self.send(f"{icon} <b>System</b>\n{message}")
