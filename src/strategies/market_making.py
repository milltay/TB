"""Market making strategy: quotes symmetric levels around mid with inventory skew."""

from __future__ import annotations

import time
import uuid

import structlog

from src.core.config import MarketMakingConfig
from src.core.state_store import StateStore
from src.core.types import OrderIntent, OrderResult, OrderType, Side
from src.strategies.base import BaseStrategy

log = structlog.get_logger()


class MarketMakingStrategy(BaseStrategy):
    """Places layered bid/ask orders around the mid price.

    Skews quotes based on current inventory to manage risk.
    Uses ALO (Add Liquidity Only) to guarantee maker rebates.
    """

    def __init__(self, config: dict, state: StateStore) -> None:
        super().__init__(config, state)
        self.cfg = MarketMakingConfig(**config)
        self.name = "market_making"
        # Track inventory per asset (notional USDC, positive=long)
        self._inventory: dict[str, float] = {a: 0.0 for a in self.cfg.assets}
        self._last_quote_time: dict[str, float] = {}

    async def on_tick(self) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        now = time.time()

        for asset in self.cfg.assets:
            # Check refresh interval
            last = self._last_quote_time.get(asset, 0)
            if (now - last) * 1000 < self.cfg.refresh_interval_ms:
                continue

            mid = self.state.get_mid(asset)
            if mid is None or mid <= 0:
                continue

            self._last_quote_time[asset] = now

            # Update inventory from positions
            pos = self.state.get_position(asset)
            if pos:
                self._inventory[asset] = pos.size * pos.entry_price
            else:
                self._inventory[asset] = 0.0

            inv = self._inventory[asset]
            inv_ratio = inv / self.cfg.max_inventory_usdc if self.cfg.max_inventory_usdc else 0

            # Reduce order size as inventory approaches max
            size_scale = max(0.1, 1.0 - abs(inv_ratio) * 0.5)
            order_size_usdc = self.cfg.order_size_usdc * size_scale
            order_size = order_size_usdc / mid

            # Skew: if long, tighten asks/widen bids to reduce inventory
            skew = inv_ratio * self.cfg.inventory_skew_factor
            spread_frac = self.cfg.spread_bps / 10000

            order_type = OrderType.ALO if self.cfg.use_alo else OrderType.LIMIT

            for level in range(self.cfg.num_levels):
                level_offset = level * (self.cfg.level_spacing_bps / 10000)

                # Bid price: mid - spread - level_offset + skew adjustment
                bid_offset = spread_frac + level_offset + skew * spread_frac
                bid_price = mid * (1 - bid_offset)

                # Ask price: mid + spread + level_offset - skew adjustment
                ask_offset = spread_frac + level_offset - skew * spread_frac
                ask_price = mid * (1 + ask_offset)

                # Skip bid if inventory too long
                if inv_ratio < 0.9:
                    intents.append(OrderIntent(
                        strategy_name=self.name,
                        asset=asset,
                        side=Side.BUY,
                        size=order_size,
                        price=bid_price,
                        order_type=order_type,
                        cloid=f"mm-{asset}-b{level}-{uuid.uuid4().hex[:8]}",
                    ))

                # Skip ask if inventory too short
                if inv_ratio > -0.9:
                    intents.append(OrderIntent(
                        strategy_name=self.name,
                        asset=asset,
                        side=Side.SELL,
                        size=order_size,
                        price=ask_price,
                        order_type=order_type,
                        cloid=f"mm-{asset}-a{level}-{uuid.uuid4().hex[:8]}",
                    ))

        return intents

    async def on_fill(self, fill: OrderResult) -> None:
        asset = fill.asset
        if asset in self._inventory:
            notional = (fill.fill_size or 0) * (fill.fill_price or 0)
            if fill.side == Side.BUY:
                self._inventory[asset] += notional
            else:
                self._inventory[asset] -= notional

        log.info(
            "mm_fill",
            asset=asset,
            side=fill.side.value,
            price=fill.fill_price,
            size=fill.fill_size,
            inventory=self._inventory.get(asset, 0),
        )
