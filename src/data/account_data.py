"""Account data subscription handlers for fills, positions, orders."""

from __future__ import annotations

from typing import Any

import structlog

from src.core.event_bus import EventBus
from src.core.state_store import StateStore
from src.core.types import OrderResult, OrderStatus, Side

log = structlog.get_logger()


class AccountDataHandler:
    """Processes WebSocket account data and updates state store."""

    def __init__(self, state: StateStore, event_bus: EventBus) -> None:
        self._state = state
        self._event_bus = event_bus

    async def handle_user_fills(self, data: dict[str, Any]) -> None:
        fills = data.get("data", [])
        for fill in fills:
            result = OrderResult(
                cloid=fill.get("cloid"),
                oid=fill.get("oid"),
                status=OrderStatus.FILLED,
                fill_price=float(fill.get("px", 0)),
                fill_size=float(fill.get("sz", 0)),
                timestamp=fill.get("time", 0),
                asset=fill.get("coin", ""),
                side=Side.BUY if fill.get("side") == "B" else Side.SELL,
            )
            log.info(
                "fill_received",
                asset=result.asset,
                side=result.side.value,
                price=result.fill_price,
                size=result.fill_size,
                cloid=result.cloid,
            )
            self._event_bus.publish_nowait("fill", result=result)

    async def handle_user_open_orders(self, data: dict[str, Any]) -> None:
        orders = data.get("data", [])
        await self._state.update_open_order_count(len(orders))

    async def handle_user_state(self, data: dict[str, Any]) -> None:
        """Handle clearinghouse state updates (positions + equity)."""
        state_data = data.get("data", {})
        # Update positions
        asset_positions = state_data.get("assetPositions", [])
        await self._state.update_positions(asset_positions)
        # Update equity
        margin_summary = state_data.get("marginSummary", {})
        equity = float(margin_summary.get("accountValue", 0))
        if equity > 0:
            await self._state.update_equity(equity)
