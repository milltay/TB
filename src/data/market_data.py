"""Market data subscription handlers for L2 book, trades, and mids."""

from __future__ import annotations

from typing import Any

import structlog

from src.core.event_bus import EventBus
from src.core.state_store import StateStore

log = structlog.get_logger()


class MarketDataHandler:
    """Processes WebSocket market data and updates state store."""

    def __init__(self, state: StateStore, event_bus: EventBus) -> None:
        self._state = state
        self._event_bus = event_bus

    async def handle_all_mids(self, data: dict[str, Any]) -> None:
        mids = data.get("data", {}).get("mids", {})
        if mids:
            await self._state.update_mids(mids)

    async def handle_l2_book(self, data: dict[str, Any]) -> None:
        book_data = data.get("data", {})
        coin = book_data.get("coin", "")
        levels = book_data.get("levels", [[], []])
        if coin and len(levels) == 2:
            bids = [[lv.get("px", "0"), lv.get("sz", "0")] for lv in levels[0]]
            asks = [[lv.get("px", "0"), lv.get("sz", "0")] for lv in levels[1]]
            await self._state.update_book(coin, bids, asks)
            self._event_bus.publish_nowait("book_update", asset=coin)

    async def handle_trades(self, data: dict[str, Any]) -> None:
        trades = data.get("data", [])
        for trade in trades:
            self._event_bus.publish_nowait(
                "trade",
                asset=trade.get("coin", ""),
                side=trade.get("side", ""),
                price=float(trade.get("px", 0)),
                size=float(trade.get("sz", 0)),
                is_liquidation=trade.get("liquidation", False),
                timestamp=trade.get("time", 0),
            )
