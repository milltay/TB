"""Main entry point and orchestrator for the Hyperliquid trading bot."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from pathlib import Path

import structlog

from src.core.config import (
    FundingArbConfig,
    GlobalConfig,
    LiquidationConfig,
    MarketMakingConfig,
    MomentumConfig,
    load_global_config,
    load_strategy_config,
)
from src.core.event_bus import EventBus
from src.core.state_store import StateStore
from src.core.types import OrderResult, OrderStatus
from src.data.account_data import AccountDataHandler
from src.data.funding import FundingDataHandler
from src.data.market_data import MarketDataHandler
from src.data.ws_manager import WSManager
from src.execution.executor import Executor
from src.execution.order_tracker import OrderTracker
from src.observability.alerts import AlertManager
from src.observability.metrics import MetricsServer
from src.observability.pnl_tracker import PnLTracker
from src.risk.risk_manager import RiskManager
from src.strategies.base import BaseStrategy
from src.strategies.funding_arb import FundingArbStrategy
from src.strategies.liquidation import LiquidationStrategy
from src.strategies.market_making import MarketMakingStrategy
from src.strategies.momentum import MomentumStrategy

log = structlog.get_logger()

STRATEGY_MAP: dict[str, type[BaseStrategy]] = {
    "market_making": MarketMakingStrategy,
    "funding_arb": FundingArbStrategy,
    "momentum": MomentumStrategy,
    "liquidation": LiquidationStrategy,
}


def init_strategies(
    config: GlobalConfig, state: StateStore, config_dir: str = "config"
) -> list[BaseStrategy]:
    strategies = []
    for name in config.strategies.enabled:
        cls = STRATEGY_MAP.get(name)
        if not cls:
            log.warning("unknown_strategy", name=name)
            continue
        strat_config = load_strategy_config(name, config_dir)
        strategy = cls(strat_config, state)
        strategies.append(strategy)
        log.info("strategy_initialized", name=name)
    return strategies


async def setup_hyperliquid(config: GlobalConfig):
    """Initialize Hyperliquid SDK objects."""
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from hyperliquid.utils import constants

    base_url = (
        constants.TESTNET_API_URL
        if config.network == "testnet"
        else constants.MAINNET_API_URL
    )

    info = Info(base_url, skip_ws=True)
    wallet = Account.from_key(config.secret_key)
    exchange = Exchange(
        wallet=wallet,
        base_url=base_url,
        account_address=config.account_address,
    )

    # Build asset index map and sz_decimals
    meta = info.meta()
    asset_map = {}
    sz_decimals_map = {}
    for i, asset_info in enumerate(meta["universe"]):
        asset_map[asset_info["name"]] = i
        sz_decimals_map[asset_info["name"]] = asset_info.get("szDecimals", 0)

    return info, exchange, asset_map, sz_decimals_map


async def main_loop(
    config: GlobalConfig,
    config_dir: str = "config",
    dry_run: bool = False,
) -> None:
    config.dry_run = dry_run

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer() if dry_run else structlog.processors.JSONRenderer(),
        ],
    )

    log.info("bot_starting", network=config.network, dry_run=dry_run)

    # Core components
    state = StateStore()
    event_bus = EventBus()

    # Data handlers
    market_handler = MarketDataHandler(state, event_bus)
    account_handler = AccountDataHandler(state, event_bus)
    funding_handler = FundingDataHandler(state, event_bus)

    # WebSocket
    ws = WSManager(config.network, config.account_address)
    ws.register_handler("allMids", market_handler.handle_all_mids)
    ws.register_handler("l2Book", market_handler.handle_l2_book)
    ws.register_handler("trades", market_handler.handle_trades)
    ws.register_handler("userFills", account_handler.handle_user_fills)
    ws.register_handler("userOpenOrders", account_handler.handle_user_open_orders)
    ws.register_handler("userFundings", funding_handler.handle_user_fundings)

    # Initialize Hyperliquid SDK (skip in dry-run without credentials)
    info = None
    exchange = None
    if not dry_run and config.secret_key and not config.secret_key.startswith("0xYOUR"):
        try:
            info, exchange, asset_map, sz_decimals_map = await setup_hyperliquid(config)
            await state.set_asset_index_map(asset_map)
            await state.set_sz_decimals(sz_decimals_map)
            log.info("hyperliquid_sdk_initialized", assets=len(asset_map))
        except Exception:
            log.exception("sdk_init_failed")
            if not dry_run:
                raise
    else:
        log.warning("running_without_sdk", reason="dry_run or missing credentials")

    # Strategies
    strategies = init_strategies(config, state, config_dir)

    # Risk + Execution
    risk = RiskManager(config, state)
    executor = Executor(config, exchange, state)
    order_tracker = OrderTracker()

    # Observability
    pnl = PnLTracker(state)
    metrics = MetricsServer(config.metrics.prometheus_port)
    alerts = AlertManager(config.alerts)

    # Wire fill events to PnL + alerts
    async def on_fill_event(result: OrderResult) -> None:
        strategy_name = order_tracker.on_result(result)
        pnl.record_fill(result)
        if strategy_name:
            for s in strategies:
                if s.name == strategy_name:
                    await s.on_fill(result)
                    break
        await alerts.alert_fill(result)

    event_bus.subscribe("fill", lambda result: on_fill_event(result))

    # Wire liquidation events to liquidation strategy
    for s in strategies:
        if isinstance(s, LiquidationStrategy):
            event_bus.subscribe(
                "trade",
                lambda asset, side, price, size, is_liquidation, timestamp, _s=s: (
                    _s.on_liquidation_trade(asset, side, price, size, timestamp)
                    if is_liquidation
                    else asyncio.sleep(0)
                ),
            )

    # Start metrics server
    try:
        metrics.start()
    except Exception:
        log.warning("metrics_server_failed_to_start")

    # Connect WebSocket
    await ws.connect()
    await ws.subscribe_all_mids()
    await ws.subscribe_user_events()

    # Subscribe to L2 books for relevant assets
    subscribed_assets = set()
    for s in strategies:
        if hasattr(s, "cfg") and hasattr(s.cfg, "assets"):
            for asset in s.cfg.assets:
                if asset not in subscribed_assets:
                    await ws.subscribe_l2_book(asset)
                    await ws.subscribe_trades(asset)
                    subscribed_assets.add(asset)

    # Start strategies
    for s in strategies:
        await s.on_start()

    # Start funding refresh task
    funding_task = None
    if info:
        funding_task = asyncio.create_task(
            funding_handler.periodic_funding_refresh(info, interval_seconds=60)
        )

    # Shutdown flag
    shutdown = asyncio.Event()

    def handle_signal(signum, frame):
        log.info("shutdown_signal_received", signal=signum)
        shutdown.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("bot_running", strategies=[s.name for s in strategies])

    # Main loop
    tick_count = 0
    try:
        while not shutdown.is_set():
            tick_start = time.time()

            all_intents = []
            for strategy in strategies:
                if not strategy.enabled:
                    continue
                try:
                    intents = await strategy.on_tick()
                    all_intents.extend(intents)
                except Exception as e:
                    await strategy.on_error(e)
                    await alerts.alert_error(str(e), context=strategy.name)

            # Risk validation
            approved = await risk.validate(all_intents)
            order_tracker.track_batch(approved)

            # Execute
            results = await executor.submit(approved)
            for result in results:
                order_tracker.on_result(result)
                if result.status == OrderStatus.FILLED:
                    pnl.record_fill(result)

            # Observability
            pnl.update()
            tick_ms = (time.time() - tick_start) * 1000
            metrics.record_tick(tick_ms)
            metrics.update_equity(state.get_equity())
            metrics.update_positions(len(state.get_all_positions()))
            metrics.update_fill_rate(order_tracker.fill_rate)

            # Check drawdown alerts
            equity = state.get_equity()
            if equity > 0:
                dd_pct = risk.circuit_breaker.get_drawdown_pct(equity)
                if dd_pct >= config.alerts.alert_on_drawdown_pct:
                    await alerts.alert_drawdown(dd_pct, equity)
                if risk.circuit_breaker.is_triggered():
                    await alerts.alert_circuit_breaker(equity)
                    await executor.cancel_all()
                    log.critical("circuit_breaker_halt")
                    break

            tick_count += 1
            elapsed = time.time() - tick_start
            sleep_time = max(0, 0.1 - elapsed)  # 100ms tick rate
            await asyncio.sleep(sleep_time)

    except Exception:
        log.exception("main_loop_error")
    finally:
        log.info("shutting_down", ticks=tick_count)
        for s in strategies:
            await s.on_stop()
        await executor.cancel_all()
        await ws.disconnect()
        await alerts.close()
        pnl.close()
        if funding_task:
            funding_task.cancel()


def run() -> None:
    parser = argparse.ArgumentParser(description="Hyperliquid Multi-Strategy Trading Bot")
    parser.add_argument("--config-dir", default="config", help="Config directory path")
    parser.add_argument("--dry-run", action="store_true", help="Log orders without submitting")
    parser.add_argument("--network", choices=["mainnet", "testnet"], help="Override network")
    args = parser.parse_args()

    config = load_global_config(args.config_dir)
    if args.network:
        config.network = args.network

    asyncio.run(main_loop(config, args.config_dir, dry_run=args.dry_run))


if __name__ == "__main__":
    run()
