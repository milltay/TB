"""Tracks open orders and matches fills to originating strategies."""

from __future__ import annotations

import time

import structlog

from src.core.types import OrderIntent, OrderResult, OrderStatus

log = structlog.get_logger()


class OrderTracker:
    """Maps cloid → strategy and tracks order lifecycle."""

    def __init__(self) -> None:
        # cloid → OrderIntent
        self._pending: dict[str, OrderIntent] = {}
        # cloid → OrderResult (filled/canceled)
        self._completed: dict[str, OrderResult] = {}
        self._total_submitted = 0
        self._total_filled = 0
        self._total_rejected = 0

    def track(self, intent: OrderIntent) -> None:
        if intent.cloid:
            self._pending[intent.cloid] = intent
            self._total_submitted += 1

    def track_batch(self, intents: list[OrderIntent]) -> None:
        for intent in intents:
            self.track(intent)

    def on_result(self, result: OrderResult) -> str | None:
        """Process an order result. Returns the strategy name if matched."""
        cloid = result.cloid
        if not cloid:
            return result.strategy_name or None

        intent = self._pending.pop(cloid, None)
        if intent:
            result.strategy_name = intent.strategy_name
            result.asset = result.asset or intent.asset
            result.side = result.side or intent.side

        if result.status == OrderStatus.FILLED:
            self._total_filled += 1
        elif result.status == OrderStatus.REJECTED:
            self._total_rejected += 1

        self._completed[cloid] = result

        # Prune old completed orders (keep last 1000)
        if len(self._completed) > 1000:
            oldest_keys = sorted(self._completed, key=lambda k: self._completed[k].timestamp)
            for k in oldest_keys[: len(self._completed) - 1000]:
                del self._completed[k]

        return result.strategy_name

    def get_strategy_for_cloid(self, cloid: str) -> str | None:
        intent = self._pending.get(cloid)
        if intent:
            return intent.strategy_name
        result = self._completed.get(cloid)
        if result:
            return result.strategy_name
        return None

    @property
    def fill_rate(self) -> float:
        if self._total_submitted == 0:
            return 0.0
        return self._total_filled / self._total_submitted

    @property
    def stats(self) -> dict:
        return {
            "submitted": self._total_submitted,
            "filled": self._total_filled,
            "rejected": self._total_rejected,
            "pending": len(self._pending),
            "fill_rate": round(self.fill_rate, 4),
        }
