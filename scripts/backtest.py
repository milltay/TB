"""Simple historical backtest harness.

Loads historical data and replays it through strategies with simulated execution.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import load_global_config, load_strategy_config
from src.core.state_store import StateStore
from src.core.types import OrderResult, OrderStatus, Side
from src.strategies.market_making import MarketMakingStrategy
from src.strategies.funding_arb import FundingArbStrategy


class BacktestEngine:
    """Replays historical price data through strategies."""

    def __init__(self, state: StateStore) -> None:
        self.state = state
        self.fills: list[dict] = []
        self.pnl = 0.0

    async def simulate_fill(self, intent, mid: float) -> OrderResult:
        """Simulate an immediate fill at mid price."""
        # Simple slippage model: 1 bps
        slippage = mid * 0.0001
        fill_price = mid + slippage if intent.side == Side.BUY else mid - slippage

        result = OrderResult(
            cloid=intent.cloid,
            oid=len(self.fills),
            status=OrderStatus.FILLED,
            fill_price=fill_price,
            fill_size=intent.size,
            asset=intent.asset,
            side=intent.side,
            strategy_name=intent.strategy_name,
        )
        self.fills.append({
            "asset": intent.asset,
            "side": intent.side.value,
            "price": fill_price,
            "size": intent.size,
        })
        return result

    async def run(self, strategy, price_series: list[dict]) -> dict:
        """Run backtest over a price series.

        Args:
            strategy: Strategy instance to test.
            price_series: List of {"timestamp": float, "prices": {"BTC": 50000, ...}}
        """
        await strategy.on_start()

        for tick in price_series:
            prices = tick["prices"]
            await self.state.update_mids({k: str(v) for k, v in prices.items()})

            intents = await strategy.on_tick()
            for intent in intents:
                mid = prices.get(intent.asset, intent.price)
                result = await self.simulate_fill(intent, mid)
                await strategy.on_fill(result)

        await strategy.on_stop()

        return {
            "total_fills": len(self.fills),
            "buy_fills": len([f for f in self.fills if f["side"] == "buy"]),
            "sell_fills": len([f for f in self.fills if f["side"] == "sell"]),
        }


async def main():
    print("=== Hyperliquid Backtest Harness ===")
    print()
    print("Usage: Provide historical price data as JSON or generate synthetic data.")
    print()

    # Generate synthetic price data for demo
    import math
    price_series = []
    base_btc = 50000
    for i in range(1000):
        # Sinusoidal price movement with trend
        btc_price = base_btc + 500 * math.sin(i / 50) + i * 0.5
        price_series.append({
            "timestamp": 1700000000 + i * 5,  # 5s intervals
            "prices": {"BTC": btc_price},
        })

    state = StateStore()
    config = {
        "assets": ["BTC"],
        "spread_bps": 3,
        "order_size_usdc": 50,
        "num_levels": 2,
        "level_spacing_bps": 2,
        "inventory_skew_factor": 0.5,
        "max_inventory_usdc": 400,
        "refresh_interval_ms": 0,
        "use_alo": True,
    }

    mm = MarketMakingStrategy(config, state)
    engine = BacktestEngine(state)
    results = await engine.run(mm, price_series)

    print(f"Results: {json.dumps(results, indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
