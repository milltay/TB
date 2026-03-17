"""Tests for the risk manager."""

import pytest

from src.core.config import GlobalConfig
from src.core.state_store import StateStore
from src.core.types import OrderIntent, OrderType, Side
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.risk_manager import RiskManager


@pytest.fixture
def config():
    return GlobalConfig(starting_balance_usdc=3400)


@pytest.fixture
def state():
    return StateStore()


@pytest.fixture
def risk(config, state):
    return RiskManager(config, state)


def make_intent(asset="BTC", side=Side.BUY, size=0.01, price=50000, strategy="test", reduce_only=False):
    return OrderIntent(
        strategy_name=strategy,
        asset=asset,
        side=side,
        size=size,
        price=price,
        order_type=OrderType.LIMIT,
        reduce_only=reduce_only,
        cloid=f"test-{asset}",
    )


@pytest.mark.asyncio
async def test_approve_within_limits(risk, state):
    await state.update_mids({"BTC": "50000"})
    intent = make_intent(size=0.01, price=50000)  # 500 USDC notional
    approved = await risk.validate([intent])
    assert len(approved) == 1


@pytest.mark.asyncio
async def test_reject_exceeds_total_exposure(risk, state):
    await state.update_mids({"BTC": "50000"})
    # 60 * 50000 = 3,000,000 notional, way over 2500 limit
    intent = make_intent(size=60, price=50000)
    approved = await risk.validate([intent])
    assert len(approved) == 0


@pytest.mark.asyncio
async def test_reject_exceeds_per_asset_exposure(risk, state):
    await state.update_mids({"BTC": "50000"})
    # 0.02 * 50000 = 1000 > 800 per-asset limit
    intent = make_intent(size=0.02, price=50000)
    approved = await risk.validate([intent])
    assert len(approved) == 0


@pytest.mark.asyncio
async def test_reject_max_open_orders(risk, state):
    await state.update_mids({"BTC": "50000"})
    await state.update_open_order_count(20)  # At max
    intent = make_intent(size=0.001, price=50000)
    approved = await risk.validate([intent])
    assert len(approved) == 0


@pytest.mark.asyncio
async def test_allow_reduce_only_bypasses_checks(risk, state):
    await state.update_mids({"BTC": "50000"})
    await state.update_open_order_count(20)
    intent = make_intent(size=0.001, price=50000, reduce_only=True)
    approved = await risk.validate([intent])
    assert len(approved) == 1


@pytest.mark.asyncio
async def test_correlation_check(risk, state):
    await state.update_mids({"BTC": "50000"})
    # Two strategies going same direction on same asset
    intent1 = make_intent(size=0.005, price=50000, strategy="strategy_a")
    intent2 = make_intent(size=0.005, price=50000, strategy="strategy_b")
    approved = await risk.validate([intent1, intent2])
    # First should pass, second should be rejected for correlation
    assert len(approved) == 1
    assert approved[0].strategy_name == "strategy_a"


@pytest.mark.asyncio
async def test_circuit_breaker_blocks_all(risk, state):
    await state.update_mids({"BTC": "50000"})
    await state.update_equity(3200)  # 200 loss = 5.88% > 5% threshold
    intent = make_intent(size=0.001, price=50000)
    approved = await risk.validate([intent])
    assert len(approved) == 0
    assert risk.circuit_breaker.is_triggered()


def test_circuit_breaker_standalone():
    cb = CircuitBreaker(starting_balance=3400, max_drawdown_pct=5.0)
    assert not cb.is_triggered()

    # Equity down 3% — should not trigger
    assert not cb.check(3298)

    # Equity down 5%+ — should trigger
    assert cb.check(3220)
    assert cb.is_triggered()

    # Stays triggered
    assert cb.check(3400)
    assert cb.is_triggered()

    # Manual reset
    cb.reset()
    assert not cb.is_triggered()
