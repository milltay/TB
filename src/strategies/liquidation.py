"""Liquidation sniping strategy: buy dips from large liquidation events."""

from __future__ import annotations

import time
import uuid

import structlog

from src.core.config import LiquidationConfig
from src.core.state_store import StateStore
from src.core.types import OrderIntent, OrderResult, OrderType, Side
from src.strategies.base import BaseStrategy

log = structlog.get_logger()


class LiquidationStrategy(BaseStrategy):
    """Detects large liquidation events and enters opposite direction.

    Quick in-and-out: tight time-based exit + profit target.
    """

    def __init__(self, config: dict, state: StateStore) -> None:
        super().__init__(config, state)
        self.cfg = LiquidationConfig(**config)
        self.name = "liquidation"
        # Pending liquidation signals: asset → {side, price, timestamp, notional}
        self._signals: list[dict] = []
        # Active positions: asset → {entry_time, entry_price, side}
        self._active: dict[str, dict] = {}

    def on_liquidation_trade(
        self, asset: str, side: str, price: float, size: float, timestamp: float
    ) -> None:
        """Called from event bus when a liquidation trade is detected."""
        if self.cfg.assets_whitelist and asset not in self.cfg.assets_whitelist:
            return

        notional = price * size
        if notional < self.cfg.min_liquidation_size_usdc:
            return

        self._signals.append({
            "asset": asset,
            "side": side,
            "price": price,
            "size": size,
            "notional": notional,
            "timestamp": timestamp,
        })
        log.info(
            "liquidation_detected",
            asset=asset,
            side=side,
            notional=round(notional, 2),
        )

    async def on_tick(self) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        now = time.time()

        # Check time-based exits for active positions
        for asset in list(self._active.keys()):
            info = self._active[asset]
            hold_time = now - info["entry_time"]

            if hold_time >= self.cfg.max_hold_seconds:
                exit_intent = self._create_exit(asset, "max_hold_time")
                if exit_intent:
                    intents.append(exit_intent)
                continue

            # Check profit target
            mid = self.state.get_mid(asset)
            if mid and info["entry_price"] > 0:
                if info["side"] == Side.BUY:
                    pnl_bps = ((mid - info["entry_price"]) / info["entry_price"]) * 10000
                else:
                    pnl_bps = ((info["entry_price"] - mid) / info["entry_price"]) * 10000

                if pnl_bps >= self.cfg.profit_target_bps:
                    exit_intent = self._create_exit(asset, "profit_target")
                    if exit_intent:
                        intents.append(exit_intent)

        # Process new signals
        while self._signals:
            signal = self._signals.pop(0)
            asset = signal["asset"]

            if asset in self._active:
                continue

            # Stale signal check (> 5 seconds old)
            if now - signal["timestamp"] > 5:
                continue

            mid = self.state.get_mid(asset)
            if not mid or mid <= 0:
                continue

            # Enter opposite direction of liquidation
            # If liquidation is a sell (forced close of long) → price drops → buy the dip
            entry_side = Side.BUY if signal["side"] in ("sell", "S", "A") else Side.SELL
            size = self.cfg.position_size_usdc / mid

            self._active[asset] = {
                "entry_time": now,
                "entry_price": mid,
                "side": entry_side,
            }

            log.info(
                "liquidation_snipe_entry",
                asset=asset,
                side=entry_side.value,
                price=mid,
            )

            intents.append(OrderIntent(
                strategy_name=self.name,
                asset=asset,
                side=entry_side,
                size=size,
                price=mid,
                order_type=OrderType.IOC,
                cloid=f"liq-{asset}-{uuid.uuid4().hex[:8]}",
            ))

        return intents

    def _create_exit(self, asset: str, reason: str) -> OrderIntent | None:
        info = self._active.pop(asset, None)
        if not info:
            return None

        pos = self.state.get_position(asset)
        mid = self.state.get_mid(asset)
        if not mid:
            return None

        size = abs(pos.size) if pos else self.cfg.position_size_usdc / mid
        exit_side = Side.SELL if info["side"] == Side.BUY else Side.BUY

        log.info("liquidation_snipe_exit", asset=asset, reason=reason)

        return OrderIntent(
            strategy_name=self.name,
            asset=asset,
            side=exit_side,
            size=size,
            price=mid,
            order_type=OrderType.IOC,
            reduce_only=True,
            cloid=f"liq-exit-{asset}-{uuid.uuid4().hex[:8]}",
        )

    async def on_fill(self, fill: OrderResult) -> None:
        log.info(
            "liquidation_fill",
            asset=fill.asset,
            side=fill.side.value,
            price=fill.fill_price,
            size=fill.fill_size,
        )
