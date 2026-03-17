"""WebSocket connection manager with reconnect logic for Hyperliquid."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Coroutine

import structlog

log = structlog.get_logger()

MAINNET_WS = "wss://api.hyperliquid.xyz/ws"
TESTNET_WS = "wss://api.hyperliquid-testnet.xyz/ws"

Handler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class WSManager:
    """Manages a persistent WebSocket connection to Hyperliquid."""

    def __init__(
        self,
        network: str,
        account_address: str,
        handlers: dict[str, Handler] | None = None,
    ) -> None:
        self._url = TESTNET_WS if network == "testnet" else MAINNET_WS
        self._account_address = account_address
        self._handlers: dict[str, Handler] = handlers or {}
        self._ws = None
        self._session = None
        self._running = False
        self._reconnect_count = 0
        self._subscriptions: list[dict] = []
        self._max_backoff = 30.0

    def register_handler(self, channel: str, handler: Handler) -> None:
        self._handlers[channel] = handler

    async def connect(self) -> None:
        import aiohttp

        self._running = True
        self._session = aiohttp.ClientSession()
        await self._connect_ws()

    async def _connect_ws(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                self._ws = await self._session.ws_connect(self._url, heartbeat=20)
                self._reconnect_count += 1
                log.info("ws_connected", url=self._url, reconnect_count=self._reconnect_count)

                # Re-subscribe on reconnect
                for sub in self._subscriptions:
                    await self._send(sub)

                # Start message loop
                asyncio.ensure_future(self._read_loop())
                return

            except Exception:
                log.exception("ws_connect_failed", backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)

    async def _read_loop(self) -> None:
        import aiohttp

        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    log.warning("ws_disconnected", type=str(msg.type))
                    break
        except Exception:
            log.exception("ws_read_error")

        if self._running:
            log.info("ws_reconnecting")
            await self._connect_ws()

    async def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("ws_invalid_json", raw=raw[:200])
            return

        channel = data.get("channel", "")
        handler = self._handlers.get(channel)
        if handler:
            try:
                await handler(data)
            except Exception:
                log.exception("ws_handler_error", channel=channel)

    async def subscribe_all_mids(self) -> None:
        sub = {"method": "subscribe", "subscription": {"type": "allMids"}}
        self._subscriptions.append(sub)
        if self._ws:
            await self._send(sub)

    async def subscribe_l2_book(self, asset: str) -> None:
        sub = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": asset}}
        self._subscriptions.append(sub)
        if self._ws:
            await self._send(sub)

    async def subscribe_user_events(self) -> None:
        user = self._account_address
        for channel_type in ["userFills", "userOpenOrders", "userFundings"]:
            sub = {"method": "subscribe", "subscription": {"type": channel_type, "user": user}}
            self._subscriptions.append(sub)
            if self._ws:
                await self._send(sub)

    async def subscribe_trades(self, asset: str) -> None:
        sub = {"method": "subscribe", "subscription": {"type": "trades", "coin": asset}}
        self._subscriptions.append(sub)
        if self._ws:
            await self._send(sub)

    async def _send(self, data: dict) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_json(data)

    async def disconnect(self) -> None:
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        log.info("ws_disconnected_gracefully")

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count
