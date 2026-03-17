"""Tests for trading strategies with mocked state store data."""

import pytest

from src.core.state_store import StateStore
from src.core.types import OrderResult, OrderStatus, Side
from src.strategies.funding_arb import FundingArbStrategy
from src.strategies.market_making import MarketMakingStrategy


@pytest.fixture
def state():
    return StateStore()


class TestMarketMaking:
    @pytest.fixture
    def mm_config(self):
        return {
            "assets": ["BTC"],
            "spread_bps": 3,
            "order_size_usdc": 50,
            "num_levels": 2,
            "level_spacing_bps": 2,
            "inventory_skew_factor": 0.5,
            "max_inventory_usdc": 400,
            "refresh_interval_ms": 0,  # No delay for tests
            "use_alo": True,
        }

    @pytest.fixture
    def mm(self, mm_config, state):
        return MarketMakingStrategy(mm_config, state)

    @pytest.mark.asyncio
    async def test_generates_orders_when_mid_available(self, mm, state):
        await state.update_mids({"BTC": "50000"})
        intents = await mm.on_tick()
        # 2 levels * 2 sides = 4 orders for BTC
        assert len(intents) == 4
        # Check we have both buys and sells
        buys = [i for i in intents if i.side == Side.BUY]
        sells = [i for i in intents if i.side == Side.SELL]
        assert len(buys) == 2
        assert len(sells) == 2

    @pytest.mark.asyncio
    async def test_no_orders_without_mid(self, mm, state):
        intents = await mm.on_tick()
        assert len(intents) == 0

    @pytest.mark.asyncio
    async def test_uses_alo_order_type(self, mm, state):
        await state.update_mids({"BTC": "50000"})
        intents = await mm.on_tick()
        from src.core.types import OrderType
        for intent in intents:
            assert intent.order_type == OrderType.ALO

    @pytest.mark.asyncio
    async def test_spread_is_correct(self, mm, state):
        await state.update_mids({"BTC": "50000"})
        intents = await mm.on_tick()
        buys = sorted([i for i in intents if i.side == Side.BUY], key=lambda x: -x.price)
        sells = sorted([i for i in intents if i.side == Side.SELL], key=lambda x: x.price)
        # Best bid should be mid * (1 - 3bps)
        expected_bid = 50000 * (1 - 0.0003)
        assert buys[0].price == pytest.approx(expected_bid, rel=1e-4)
        # Best ask should be mid * (1 + 3bps)
        expected_ask = 50000 * (1 + 0.0003)
        assert sells[0].price == pytest.approx(expected_ask, rel=1e-4)

    @pytest.mark.asyncio
    async def test_inventory_tracking_on_fill(self, mm, state):
        await state.update_mids({"BTC": "50000"})
        fill = OrderResult(
            cloid="test", oid=1, status=OrderStatus.FILLED,
            fill_price=50000, fill_size=0.001,
            asset="BTC", side=Side.BUY, strategy_name="market_making",
        )
        await mm.on_fill(fill)
        assert mm._inventory["BTC"] == pytest.approx(50.0)


class TestFundingArb:
    @pytest.fixture
    def fa_config(self):
        return {
            "min_funding_rate_annualized": 15.0,
            "position_size_usdc": 300,
            "max_concurrent_positions": 3,
            "check_interval_seconds": 0,  # No delay for tests
            "exit_threshold_annualized": 5.0,
            "assets_whitelist": [],
            "delta_neutral": False,
        }

    @pytest.fixture
    def fa(self, fa_config, state):
        return FundingArbStrategy(fa_config, state)

    @pytest.mark.asyncio
    async def test_enters_short_on_high_positive_funding(self, fa, state):
        # High positive funding rate: 0.02% per 8h = ~21.9% annualized
        await state.update_funding("BTC", 0.0002, 0.0002)
        await state.update_mids({"BTC": "50000"})
        intents = await fa.on_tick()
        assert len(intents) == 1
        assert intents[0].side == Side.SELL
        assert intents[0].asset == "BTC"

    @pytest.mark.asyncio
    async def test_enters_long_on_high_negative_funding(self, fa, state):
        await state.update_funding("ETH", -0.0002, -0.0002)
        await state.update_mids({"ETH": "3000"})
        intents = await fa.on_tick()
        assert len(intents) == 1
        assert intents[0].side == Side.BUY

    @pytest.mark.asyncio
    async def test_no_entry_on_low_funding(self, fa, state):
        # Low funding: 0.001% per 8h = ~1.095% annualized < 15%
        await state.update_funding("BTC", 0.00001, 0.00001)
        await state.update_mids({"BTC": "50000"})
        intents = await fa.on_tick()
        assert len(intents) == 0

    @pytest.mark.asyncio
    async def test_respects_max_concurrent_positions(self, fa, state):
        await state.update_mids({"A": "100", "B": "100", "C": "100", "D": "100"})
        for asset in ["A", "B", "C", "D"]:
            await state.update_funding(asset, 0.0005, 0.0005)

        intents = await fa.on_tick()
        assert len(intents) <= 3
