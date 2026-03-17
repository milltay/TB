"""Normalized in-memory state with copy-on-write reads."""

from __future__ import annotations

import asyncio
import copy
import time

import structlog

from src.core.types import BookLevel, BookSnapshot, FundingSnapshot, Position

log = structlog.get_logger()


class StateStore:
    """Single-writer, multi-reader state store.

    Writes acquire a lock and swap snapshots atomically.
    Reads return immutable snapshots (no lock needed).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._books: dict[str, BookSnapshot] = {}
        self._positions: dict[str, Position] = {}
        self._funding: dict[str, FundingSnapshot] = {}
        self._equity: float = 0.0
        self._mids: dict[str, float] = {}
        self._open_order_count: int = 0
        self._asset_index_map: dict[str, int] = {}

    # --- Write methods (called from data layer) ---

    async def update_book(self, asset: str, bids: list[list], asks: list[list]) -> None:
        parsed_bids = [BookLevel(price=float(b[0]), size=float(b[1])) for b in bids]
        parsed_asks = [BookLevel(price=float(a[0]), size=float(a[1])) for a in asks]
        mid = (parsed_bids[0].price + parsed_asks[0].price) / 2 if parsed_bids and parsed_asks else 0.0
        spread = (parsed_asks[0].price - parsed_bids[0].price) if parsed_bids and parsed_asks else 0.0

        snapshot = BookSnapshot(
            asset=asset,
            bids=parsed_bids,
            asks=parsed_asks,
            mid_price=mid,
            spread=spread,
            timestamp=time.time(),
        )
        async with self._lock:
            self._books[asset] = snapshot
            self._mids[asset] = mid

    async def update_mids(self, mids: dict[str, str]) -> None:
        async with self._lock:
            for asset, price_str in mids.items():
                self._mids[asset] = float(price_str)

    async def update_positions(self, positions: list[dict]) -> None:
        new_positions: dict[str, Position] = {}
        for p in positions:
            pos = p.get("position", p)
            coin = pos.get("coin", "")
            szi = float(pos.get("szi", 0))
            entry_px = float(pos.get("entryPx", 0))
            unrealized_pnl = float(pos.get("unrealizedPnl", 0))
            leverage_data = pos.get("leverage", {})
            leverage_val = int(leverage_data.get("value", 1)) if isinstance(leverage_data, dict) else int(leverage_data)
            liq_px = pos.get("liquidationPx")
            liq_price = float(liq_px) if liq_px else None
            new_positions[coin] = Position(
                asset=coin,
                size=szi,
                entry_price=entry_px,
                unrealized_pnl=unrealized_pnl,
                leverage=leverage_val,
                liquidation_price=liq_price,
            )
        async with self._lock:
            self._positions = new_positions

    async def update_funding(self, asset: str, rate: float, predicted: float) -> None:
        snapshot = FundingSnapshot(
            asset=asset,
            current_rate=rate,
            predicted_rate=predicted,
        )
        async with self._lock:
            self._funding[asset] = snapshot

    async def update_equity(self, equity: float) -> None:
        async with self._lock:
            self._equity = equity

    async def update_open_order_count(self, count: int) -> None:
        async with self._lock:
            self._open_order_count = count

    async def set_asset_index_map(self, mapping: dict[str, int]) -> None:
        async with self._lock:
            self._asset_index_map = dict(mapping)

    # --- Read methods (lock-free, return copies) ---

    def get_book(self, asset: str) -> BookSnapshot | None:
        book = self._books.get(asset)
        return copy.copy(book) if book else None

    def get_position(self, asset: str) -> Position | None:
        pos = self._positions.get(asset)
        return copy.copy(pos) if pos else None

    def get_all_positions(self) -> list[Position]:
        return [copy.copy(p) for p in self._positions.values()]

    def get_funding(self, asset: str) -> FundingSnapshot | None:
        fs = self._funding.get(asset)
        return copy.copy(fs) if fs else None

    def get_all_funding(self) -> dict[str, FundingSnapshot]:
        return {k: copy.copy(v) for k, v in self._funding.items()}

    def get_mid(self, asset: str) -> float | None:
        return self._mids.get(asset)

    def get_equity(self) -> float:
        return self._equity

    def get_open_order_count(self) -> int:
        return self._open_order_count

    def get_asset_index(self, asset: str) -> int | None:
        return self._asset_index_map.get(asset)

    def get_asset_index_map(self) -> dict[str, int]:
        return dict(self._asset_index_map)
