"""Risk manager: validates order intents against portfolio limits."""

from __future__ import annotations

import structlog

from src.core.config import GlobalConfig
from src.core.state_store import StateStore
from src.core.types import OrderIntent, Side
from src.risk.circuit_breaker import CircuitBreaker

log = structlog.get_logger()


class RiskManager:
    """Filters order intents based on risk limits.

    Checks:
    - Total exposure limit
    - Per-asset exposure limit
    - Max open orders
    - Correlation (same-direction stacking across strategies)
    - Circuit breaker (drawdown kill switch)
    """

    def __init__(self, config: GlobalConfig, state: StateStore) -> None:
        self._config = config
        self._state = state
        self.circuit_breaker = CircuitBreaker(
            starting_balance=config.starting_balance_usdc,
            max_drawdown_pct=config.risk.max_drawdown_pct,
        )
        self._rejected_count = 0

    async def validate(self, intents: list[OrderIntent]) -> list[OrderIntent]:
        """Validate a batch of order intents. Returns approved intents."""
        # Check circuit breaker first
        equity = self._state.get_equity()
        if equity > 0 and self.circuit_breaker.check(equity):
            log.critical("all_orders_rejected_circuit_breaker")
            self._rejected_count += len(intents)
            return []

        approved: list[OrderIntent] = []
        # Track pending exposure from already-approved intents in this batch
        batch_exposure: dict[str, float] = {}
        batch_directions: dict[str, set[str]] = {}  # asset → set of sides from strategies

        for intent in intents:
            reason = self._check_intent(intent, batch_exposure, batch_directions)
            if reason:
                log.warning(
                    "order_rejected",
                    strategy=intent.strategy_name,
                    asset=intent.asset,
                    side=intent.side.value,
                    size=intent.size,
                    reason=reason,
                )
                self._rejected_count += 1
            else:
                approved.append(intent)
                # Track approved exposure
                mid = self._state.get_mid(intent.asset) or intent.price
                notional = intent.size * mid
                batch_exposure[intent.asset] = batch_exposure.get(intent.asset, 0) + notional
                batch_directions.setdefault(intent.asset, set()).add(
                    f"{intent.strategy_name}:{intent.side.value}"
                )

        return approved

    def _check_intent(
        self,
        intent: OrderIntent,
        batch_exposure: dict[str, float],
        batch_directions: dict[str, set[str]],
    ) -> str | None:
        """Returns rejection reason or None if approved."""
        mid = self._state.get_mid(intent.asset) or intent.price
        notional = intent.size * mid

        # Skip reduce-only orders from most checks
        if intent.reduce_only:
            return None

        # Check total exposure
        total_exposure = self._calculate_total_exposure()
        total_exposure += sum(batch_exposure.values())
        if total_exposure + notional > self._config.risk.max_total_exposure_usdc:
            return "max_total_exposure"

        # Check per-asset exposure
        asset_exposure = self._get_asset_exposure(intent.asset)
        asset_exposure += batch_exposure.get(intent.asset, 0)
        if asset_exposure + notional > self._config.risk.max_per_asset_exposure_usdc:
            return "max_per_asset_exposure"

        # Check max open orders
        current_orders = self._state.get_open_order_count()
        if current_orders >= self._config.risk.max_open_orders:
            return "max_open_orders"

        # Check correlation (prevent same-direction stacking from different strategies)
        if self._config.risk.correlation_check:
            existing = batch_directions.get(intent.asset, set())
            for entry in existing:
                strat_name, side_str = entry.split(":")
                if strat_name != intent.strategy_name and side_str == intent.side.value:
                    return "correlation_same_direction"

        return None

    def _calculate_total_exposure(self) -> float:
        """Calculate total open notional exposure."""
        total = 0.0
        for pos in self._state.get_all_positions():
            mid = self._state.get_mid(pos.asset) or pos.entry_price
            total += abs(pos.size) * mid
        return total

    def _get_asset_exposure(self, asset: str) -> float:
        pos = self._state.get_position(asset)
        if not pos:
            return 0.0
        mid = self._state.get_mid(asset) or pos.entry_price
        return abs(pos.size) * mid

    @property
    def rejected_count(self) -> int:
        return self._rejected_count
