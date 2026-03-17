"""Discord webhook alerts for fills, errors, and drawdown warnings."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp
import structlog

from src.core.config import AlertsConfig
from src.core.types import OrderResult

log = structlog.get_logger()


class AlertManager:
    """Sends alerts to Discord via webhooks."""

    def __init__(self, config: AlertsConfig) -> None:
        self._config = config
        self._webhook_url = config.discord_webhook_url
        self._session: aiohttp.ClientSession | None = None
        self._last_alert_time: dict[str, float] = {}
        self._min_alert_interval = 5.0  # Rate limit: 1 alert per type per 5s

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _send_webhook(self, content: str, embeds: list[dict] | None = None) -> None:
        if not self._webhook_url:
            return

        session = await self._ensure_session()
        payload: dict[str, Any] = {"content": content}
        if embeds:
            payload["embeds"] = embeds

        try:
            async with session.post(self._webhook_url, json=payload) as resp:
                if resp.status == 429:
                    retry_after = (await resp.json()).get("retry_after", 5)
                    log.warning("discord_rate_limited", retry_after=retry_after)
                    await asyncio.sleep(retry_after)
                elif resp.status >= 400:
                    log.warning("discord_alert_failed", status=resp.status)
        except Exception:
            log.exception("discord_alert_error")

    def _should_alert(self, alert_type: str) -> bool:
        now = time.time()
        last = self._last_alert_time.get(alert_type, 0)
        if now - last < self._min_alert_interval:
            return False
        self._last_alert_time[alert_type] = now
        return True

    async def alert_fill(self, result: OrderResult) -> None:
        if not self._config.alert_on_fill:
            return
        if not self._should_alert(f"fill:{result.asset}"):
            return

        side_emoji = "BUY" if result.side.value == "buy" else "SELL"
        content = (
            f"**Fill** | {result.strategy_name} | {side_emoji} {result.asset} | "
            f"Price: {result.fill_price} | Size: {result.fill_size}"
        )
        await self._send_webhook(content)

    async def alert_error(self, error: str, context: str = "") -> None:
        if not self._config.alert_on_error:
            return
        if not self._should_alert("error"):
            return

        content = f"**ERROR** | {context} | {error}"
        await self._send_webhook(content)

    async def alert_drawdown(self, drawdown_pct: float, equity: float) -> None:
        if not self._should_alert("drawdown"):
            return

        content = (
            f"**DRAWDOWN WARNING** | {drawdown_pct:.2f}% drawdown | "
            f"Equity: ${equity:.2f}"
        )
        await self._send_webhook(content)

    async def alert_circuit_breaker(self, equity: float) -> None:
        content = (
            f"**CIRCUIT BREAKER TRIGGERED** | "
            f"Equity: ${equity:.2f} | All trading halted. Manual restart required."
        )
        await self._send_webhook(content)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
