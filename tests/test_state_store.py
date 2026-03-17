"""Tests for the state store."""

import asyncio

import pytest

from src.core.state_store import StateStore


@pytest.fixture
def state():
    return StateStore()


@pytest.mark.asyncio
async def test_update_and_get_book(state):
    bids = [["50000.0", "1.5"], ["49999.0", "2.0"]]
    asks = [["50001.0", "1.0"], ["50002.0", "3.0"]]
    await state.update_book("BTC", bids, asks)

    book = state.get_book("BTC")
    assert book is not None
    assert book.asset == "BTC"
    assert book.mid_price == pytest.approx(50000.5)
    assert book.spread == pytest.approx(1.0)
    assert len(book.bids) == 2
    assert len(book.asks) == 2
    assert book.bids[0].price == 50000.0
    assert book.asks[0].price == 50001.0


@pytest.mark.asyncio
async def test_get_book_returns_none_for_unknown(state):
    assert state.get_book("UNKNOWN") is None


@pytest.mark.asyncio
async def test_update_and_get_mids(state):
    await state.update_mids({"BTC": "50000.5", "ETH": "3000.25"})
    assert state.get_mid("BTC") == pytest.approx(50000.5)
    assert state.get_mid("ETH") == pytest.approx(3000.25)
    assert state.get_mid("SOL") is None


@pytest.mark.asyncio
async def test_update_and_get_positions(state):
    positions = [
        {
            "position": {
                "coin": "BTC",
                "szi": "0.1",
                "entryPx": "50000",
                "unrealizedPnl": "100",
                "leverage": {"value": 3},
                "liquidationPx": "45000",
            }
        },
        {
            "position": {
                "coin": "ETH",
                "szi": "-5.0",
                "entryPx": "3000",
                "unrealizedPnl": "-50",
                "leverage": {"value": 5},
                "liquidationPx": "3500",
            }
        },
    ]
    await state.update_positions(positions)

    btc = state.get_position("BTC")
    assert btc is not None
    assert btc.size == pytest.approx(0.1)
    assert btc.entry_price == pytest.approx(50000)
    assert btc.leverage == 3

    eth = state.get_position("ETH")
    assert eth is not None
    assert eth.size == pytest.approx(-5.0)

    all_pos = state.get_all_positions()
    assert len(all_pos) == 2


@pytest.mark.asyncio
async def test_update_and_get_funding(state):
    await state.update_funding("BTC", 0.0001, 0.00015)
    fs = state.get_funding("BTC")
    assert fs is not None
    assert fs.current_rate == pytest.approx(0.0001)
    assert fs.predicted_rate == pytest.approx(0.00015)


@pytest.mark.asyncio
async def test_equity(state):
    await state.update_equity(3500.0)
    assert state.get_equity() == pytest.approx(3500.0)


@pytest.mark.asyncio
async def test_asset_index_map(state):
    await state.set_asset_index_map({"BTC": 0, "ETH": 1, "SOL": 2})
    assert state.get_asset_index("BTC") == 0
    assert state.get_asset_index("ETH") == 1
    assert state.get_asset_index("UNKNOWN") is None


@pytest.mark.asyncio
async def test_read_returns_copies(state):
    """Reads should return copies, not references to internal state."""
    await state.update_mids({"BTC": "50000"})
    bids = [["50000.0", "1.0"]]
    asks = [["50001.0", "1.0"]]
    await state.update_book("BTC", bids, asks)

    book1 = state.get_book("BTC")
    book2 = state.get_book("BTC")
    assert book1 is not book2
