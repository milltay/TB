"""Tests for the execution engine."""

import pytest

from src.core.config import GlobalConfig
from src.core.state_store import StateStore
from src.core.types import OrderIntent, OrderStatus, OrderType, Side
from src.execution.executor import Executor
from src.execution.order_tracker import OrderTracker
from src.execution.retry import retry_with_backoff


@pytest.fixture
def config():
    return GlobalConfig(dry_run=True)


@pytest.fixture
def state():
    return StateStore()


@pytest.fixture
def executor(config, state):
    return Executor(config, exchange=None, state=state)


def make_intent(asset="BTC", cloid="test-1"):
    return OrderIntent(
        strategy_name="test",
        asset=asset,
        side=Side.BUY,
        size=0.01,
        price=50000,
        order_type=OrderType.LIMIT,
        cloid=cloid,
    )


class TestExecutor:
    @pytest.mark.asyncio
    async def test_dry_run_accepts_all(self, executor):
        intents = [make_intent(cloid=f"t-{i}") for i in range(5)]
        results = await executor.submit(intents)
        assert len(results) == 5
        for r in results:
            assert r.status == OrderStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_empty_intents(self, executor):
        results = await executor.submit([])
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_batching(self, executor):
        # batch_size=10, so 25 intents should create 3 batches
        intents = [make_intent(cloid=f"t-{i}") for i in range(25)]
        results = await executor.submit(intents)
        assert len(results) == 25

    @pytest.mark.asyncio
    async def test_tif_mapping(self):
        assert Executor._get_tif(OrderType.ALO) == {"limit": {"tif": "Alo"}}
        assert Executor._get_tif(OrderType.IOC) == {"limit": {"tif": "Ioc"}}
        assert Executor._get_tif(OrderType.LIMIT) == {"limit": {"tif": "Gtc"}}


class TestOrderTracker:
    def test_track_and_match(self):
        tracker = OrderTracker()
        intent = make_intent(cloid="abc")
        tracker.track(intent)

        from src.core.types import OrderResult
        result = OrderResult(cloid="abc", oid=1, status=OrderStatus.FILLED,
                            fill_price=50000, fill_size=0.01)
        strategy = tracker.on_result(result)
        assert strategy == "test"
        assert result.strategy_name == "test"

    def test_fill_rate(self):
        tracker = OrderTracker()
        for i in range(10):
            tracker.track(make_intent(cloid=f"o-{i}"))

        from src.core.types import OrderResult
        for i in range(7):
            tracker.on_result(OrderResult(cloid=f"o-{i}", oid=i, status=OrderStatus.FILLED))
        for i in range(7, 10):
            tracker.on_result(OrderResult(cloid=f"o-{i}", oid=i, status=OrderStatus.REJECTED))

        assert tracker.fill_rate == pytest.approx(0.7)
        stats = tracker.stats
        assert stats["filled"] == 7
        assert stats["rejected"] == 3

    def test_prune_old_completed(self):
        tracker = OrderTracker()
        from src.core.types import OrderResult
        for i in range(1100):
            tracker.track(make_intent(cloid=f"o-{i}"))
            tracker.on_result(OrderResult(cloid=f"o-{i}", oid=i, status=OrderStatus.FILLED))
        assert len(tracker._completed) <= 1000


class TestRetry:
    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        call_count = 0
        def fn():
            nonlocal call_count
            call_count += 1
            return "ok"
        result = await retry_with_backoff(fn, max_attempts=3, base_ms=10)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        call_count = 0
        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("fail")
            return "ok"
        result = await retry_with_backoff(fn, max_attempts=3, base_ms=10)
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self):
        def fn():
            raise ValueError("always fails")
        with pytest.raises(ValueError, match="always fails"):
            await retry_with_backoff(fn, max_attempts=2, base_ms=10)
