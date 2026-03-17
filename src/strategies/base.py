"""Abstract base strategy interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

import structlog

from src.core.state_store import StateStore
from src.core.types import OrderIntent, OrderResult

log = structlog.get_logger()


class BaseStrategy(ABC):
    """Base class for all trading strategies."""

    def __init__(self, config: dict, state: StateStore) -> None:
        self.config = config
        self.state = state
        self.name = self.__class__.__name__
        self.enabled = True

    @abstractmethod
    async def on_tick(self) -> list[OrderIntent]:
        """Called every tick. Return order intents to submit."""
        ...

    async def on_fill(self, fill: OrderResult) -> None:
        """Called when an order from this strategy is filled."""

    async def on_start(self) -> None:
        """Called once at startup."""
        log.info("strategy_started", strategy=self.name)

    async def on_stop(self) -> None:
        """Called on graceful shutdown."""
        log.info("strategy_stopped", strategy=self.name)

    async def on_error(self, error: Exception) -> None:
        """Called on strategy-level error."""
        log.error("strategy_error", strategy=self.name, error=str(error))
