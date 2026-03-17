"""Order execution engine: batches and submits orders to Hyperliquid."""

from __future__ import annotations

import asyncio
import time
import uuid

import structlog

from src.core.config import GlobalConfig
from src.core.state_store import StateStore
from src.core.types import OrderIntent, OrderResult, OrderStatus, OrderType, Side
from src.execution.retry import retry_with_backoff

log = structlog.get_logger()


class Executor:
    """Converts OrderIntents to Hyperliquid orders and submits them.

    Handles batching, rate limiting, and retry logic.
    """

    def __init__(self, config: GlobalConfig, exchange=None, state: StateStore | None = None) -> None:
        self._config = config
        self._exchange = exchange
        self._state = state
        self._rate_weight_used = 0
        self._rate_window_start = time.time()
        self._rate_limit = 1200  # weight per minute
        self._dry_run = config.dry_run

    async def submit(self, intents: list[OrderIntent]) -> list[OrderResult]:
        """Submit a batch of order intents. Returns results."""
        if not intents:
            return []

        results: list[OrderResult] = []

        # Group by asset for efficient batching
        batches = self._group_into_batches(intents)

        for batch in batches:
            # Check rate limit
            if not self._check_rate_limit(batch):
                log.warning("rate_limit_approaching", weight_used=self._rate_weight_used)
                await asyncio.sleep(1)

            batch_results = await self._submit_batch(batch)
            results.extend(batch_results)

        return results

    def _group_into_batches(self, intents: list[OrderIntent]) -> list[list[OrderIntent]]:
        """Group intents into batches respecting batch_size."""
        batch_size = self._config.execution.batch_size
        batches = []
        for i in range(0, len(intents), batch_size):
            batches.append(intents[i : i + batch_size])
        return batches

    async def _submit_batch(self, intents: list[OrderIntent]) -> list[OrderResult]:
        """Submit a single batch of orders."""
        results = []

        for intent in intents:
            if self._dry_run:
                log.info(
                    "dry_run_order",
                    strategy=intent.strategy_name,
                    asset=intent.asset,
                    side=intent.side.value,
                    size=intent.size,
                    price=intent.price,
                    order_type=intent.order_type.value,
                )
                results.append(OrderResult(
                    cloid=intent.cloid,
                    oid=None,
                    status=OrderStatus.ACCEPTED,
                    asset=intent.asset,
                    side=intent.side,
                    strategy_name=intent.strategy_name,
                ))
                continue

            if not self._exchange:
                log.error("no_exchange_configured")
                results.append(OrderResult(
                    cloid=intent.cloid,
                    oid=None,
                    status=OrderStatus.REJECTED,
                    asset=intent.asset,
                    side=intent.side,
                    strategy_name=intent.strategy_name,
                ))
                continue

            result = await self._submit_single(intent)
            results.append(result)

        # Track rate limit weight
        weight = 1 + len(intents) // 40
        self._rate_weight_used += weight

        return results

    async def _submit_single(self, intent: OrderIntent) -> OrderResult:
        """Submit a single order with retry logic."""
        asset_index = self._state.get_asset_index(intent.asset) if self._state else None
        if asset_index is None:
            log.error("unknown_asset_index", asset=intent.asset)
            return OrderResult(
                cloid=intent.cloid,
                oid=None,
                status=OrderStatus.REJECTED,
                asset=intent.asset,
                side=intent.side,
                strategy_name=intent.strategy_name,
            )

        is_buy = intent.side == Side.BUY
        # Map order type to Hyperliquid TIF
        tif = self._get_tif(intent.order_type)

        order_spec = {
            "coin": intent.asset,
            "is_buy": is_buy,
            "sz": intent.size,
            "limit_px": intent.price,
            "order_type": tif,
            "reduce_only": intent.reduce_only,
        }
        if intent.cloid:
            order_spec["cloid"] = intent.cloid

        async def _do_submit():
            return self._exchange.order(
                order_spec["coin"],
                order_spec["is_buy"],
                order_spec["sz"],
                order_spec["limit_px"],
                order_spec["order_type"],
                reduce_only=order_spec["reduce_only"],
            )

        try:
            response = await retry_with_backoff(
                _do_submit,
                max_attempts=self._config.execution.retry_attempts,
                base_ms=self._config.execution.retry_backoff_base_ms,
            )
            # Parse response
            status = response.get("status", "")
            if status == "ok":
                statuses = response.get("response", {}).get("data", {}).get("statuses", [])
                if statuses and statuses[0].get("filled"):
                    return OrderResult(
                        cloid=intent.cloid,
                        oid=statuses[0].get("filled", {}).get("oid"),
                        status=OrderStatus.FILLED,
                        fill_price=float(statuses[0]["filled"].get("avgPx", 0)),
                        fill_size=float(statuses[0]["filled"].get("totalSz", 0)),
                        asset=intent.asset,
                        side=intent.side,
                        strategy_name=intent.strategy_name,
                    )
                return OrderResult(
                    cloid=intent.cloid,
                    oid=None,
                    status=OrderStatus.ACCEPTED,
                    asset=intent.asset,
                    side=intent.side,
                    strategy_name=intent.strategy_name,
                )
            else:
                log.warning("order_rejected_by_exchange", response=response, intent=intent.cloid)
                return OrderResult(
                    cloid=intent.cloid,
                    oid=None,
                    status=OrderStatus.REJECTED,
                    asset=intent.asset,
                    side=intent.side,
                    strategy_name=intent.strategy_name,
                )

        except Exception as e:
            log.exception("order_submission_failed", cloid=intent.cloid)
            return OrderResult(
                cloid=intent.cloid,
                oid=None,
                status=OrderStatus.REJECTED,
                asset=intent.asset,
                side=intent.side,
                strategy_name=intent.strategy_name,
            )

    @staticmethod
    def _get_tif(order_type: OrderType) -> dict:
        if order_type == OrderType.ALO:
            return {"limit": {"tif": "Alo"}}
        elif order_type == OrderType.IOC:
            return {"limit": {"tif": "Ioc"}}
        return {"limit": {"tif": "Gtc"}}

    def _check_rate_limit(self, batch: list[OrderIntent]) -> bool:
        now = time.time()
        # Reset window every 60 seconds
        if now - self._rate_window_start >= 60:
            self._rate_weight_used = 0
            self._rate_window_start = now

        weight = 1 + len(batch) // 40
        threshold = self._rate_limit * 0.8  # 80% threshold
        return (self._rate_weight_used + weight) < threshold

    async def cancel_all(self) -> None:
        """Cancel all open orders."""
        if self._dry_run or not self._exchange:
            log.info("cancel_all_orders", dry_run=self._dry_run)
            return

        try:
            open_orders = self._exchange.get_open_orders()
            for order in open_orders:
                self._exchange.cancel(order["coin"], order["oid"])
            log.info("all_orders_canceled", count=len(open_orders))
        except Exception:
            log.exception("cancel_all_failed")
