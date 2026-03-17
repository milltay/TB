"""Funding rate arbitrage strategy: capture extreme funding rates."""

from __future__ import annotations

import time
import uuid

import structlog

from src.core.config import FundingArbConfig
from src.core.state_store import StateStore
from src.core.types import OrderIntent, OrderResult, OrderType, Side
from src.strategies.base import BaseStrategy

log = structlog.get_logger()

# Funding rates on Hyperliquid are 8-hourly → 3 payments/day → 1095/year
FUNDING_PERIODS_PER_YEAR = 1095


class FundingArbStrategy(BaseStrategy):
    """Single-leg funding rate arbitrage.

    When funding is significantly positive: short to receive funding.
    When funding is significantly negative: long to receive funding.

    WARNING: This is not delta-neutral without a spot hedge on another venue.
    Mark-to-market losses can exceed accumulated funding.
    """

    def __init__(self, config: dict, state: StateStore) -> None:
        super().__init__(config, state)
        self.cfg = FundingArbConfig(**config)
        self.name = "funding_arb"
        self._last_check: float = 0
        # Track active positions: asset → entry_time
        self._active: dict[str, float] = {}
        self._accumulated_funding: dict[str, float] = {}

    async def on_tick(self) -> list[OrderIntent]:
        now = time.time()
        if now - self._last_check < self.cfg.check_interval_seconds:
            return []
        self._last_check = now

        intents: list[OrderIntent] = []

        # Check for exit conditions on active positions
        intents.extend(self._check_exits())

        # Look for new entries if under max positions
        if len(self._active) < self.cfg.max_concurrent_positions:
            intents.extend(self._scan_opportunities())

        return intents

    def _scan_opportunities(self) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        all_funding = self.state.get_all_funding()
        pending_count = len(self._active)

        for asset, fs in all_funding.items():
            if pending_count + len(intents) >= self.cfg.max_concurrent_positions:
                break
            if asset in self._active:
                continue
            if self.cfg.assets_whitelist and asset not in self.cfg.assets_whitelist:
                continue

            annualized = abs(fs.current_rate) * FUNDING_PERIODS_PER_YEAR * 100
            if annualized < self.cfg.min_funding_rate_annualized:
                continue

            mid = self.state.get_mid(asset)
            if not mid or mid <= 0:
                continue

            size = self.cfg.position_size_usdc / mid

            # Positive funding → shorts pay longs → go short to receive
            # Negative funding → longs pay shorts → go long to receive
            side = Side.SELL if fs.current_rate > 0 else Side.BUY

            log.info(
                "funding_arb_entry",
                asset=asset,
                side=side.value,
                annualized_rate=annualized,
                funding_rate=fs.current_rate,
            )

            intents.append(OrderIntent(
                strategy_name=self.name,
                asset=asset,
                side=side,
                size=size,
                price=mid,  # Market-like via IOC
                order_type=OrderType.IOC,
                cloid=f"fa-{asset}-{uuid.uuid4().hex[:8]}",
            ))

        return intents

    def _check_exits(self) -> list[OrderIntent]:
        intents: list[OrderIntent] = []

        for asset in list(self._active.keys()):
            fs = self.state.get_funding(asset)
            pos = self.state.get_position(asset)
            if not pos or abs(pos.size) < 1e-10:
                self._active.pop(asset, None)
                continue

            should_exit = False
            reason = ""

            # Check if funding has normalized
            if fs:
                annualized = abs(fs.current_rate) * FUNDING_PERIODS_PER_YEAR * 100
                if annualized < self.cfg.exit_threshold_annualized:
                    should_exit = True
                    reason = "funding_normalized"

            # Check if unrealized loss exceeds accumulated funding
            accumulated = self._accumulated_funding.get(asset, 0)
            if pos.unrealized_pnl < 0 and abs(pos.unrealized_pnl) > accumulated * 1.5:
                should_exit = True
                reason = "loss_exceeds_funding"

            if should_exit:
                mid = self.state.get_mid(asset) or pos.entry_price
                side = Side.SELL if pos.size > 0 else Side.BUY
                log.info("funding_arb_exit", asset=asset, reason=reason)
                intents.append(OrderIntent(
                    strategy_name=self.name,
                    asset=asset,
                    side=side,
                    size=abs(pos.size),
                    price=mid,
                    order_type=OrderType.IOC,
                    reduce_only=True,
                    cloid=f"fa-exit-{asset}-{uuid.uuid4().hex[:8]}",
                ))
                self._active.pop(asset, None)

        return intents

    async def on_fill(self, fill: OrderResult) -> None:
        asset = fill.asset
        if asset and "exit" not in (fill.cloid or ""):
            self._active[asset] = time.time()
            self._accumulated_funding.setdefault(asset, 0)
        log.info(
            "funding_arb_fill",
            asset=asset,
            side=fill.side.value,
            price=fill.fill_price,
            size=fill.fill_size,
        )
