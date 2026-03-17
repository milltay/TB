"""Funding rate data: snapshots and historical fetch."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from src.core.event_bus import EventBus
from src.core.state_store import StateStore

log = structlog.get_logger()


class FundingDataHandler:
    """Processes funding rate data and provides historical fetch."""

    def __init__(self, state: StateStore, event_bus: EventBus) -> None:
        self._state = state
        self._event_bus = event_bus

    async def handle_user_fundings(self, data: dict[str, Any]) -> None:
        fundings = data.get("data", [])
        for f in fundings:
            asset = f.get("coin", "")
            rate = float(f.get("fundingRate", 0))
            await self._state.update_funding(asset, rate, rate)

    async def fetch_all_funding_rates(self, info) -> None:
        """Fetch current funding rates for all assets via REST.

        Args:
            info: Hyperliquid SDK info object.
        """
        try:
            meta_and_ctx = info.meta_and_asset_ctxs()
            meta = meta_and_ctx[0]
            ctxs = meta_and_ctx[1]

            for asset_meta, ctx in zip(meta["universe"], ctxs):
                asset = asset_meta["name"]
                funding_rate = float(ctx.get("funding", 0))
                predicted = float(ctx.get("premium", 0))
                await self._state.update_funding(asset, funding_rate, predicted)

        except Exception:
            log.exception("fetch_funding_rates_failed")

    async def periodic_funding_refresh(self, info, interval_seconds: int = 60) -> None:
        """Periodically refresh funding rates."""
        while True:
            await self.fetch_all_funding_rates(info)
            await asyncio.sleep(interval_seconds)
