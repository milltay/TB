"""Prometheus metrics exposition."""

from __future__ import annotations

import structlog
from prometheus_client import Counter, Gauge, Histogram, start_http_server

log = structlog.get_logger()

# PnL
total_pnl_usdc = Gauge("hlbot_total_pnl_usdc", "Total PnL in USDC")
strategy_pnl_usdc = Gauge("hlbot_strategy_pnl_usdc", "PnL per strategy", ["strategy"])
equity_usdc = Gauge("hlbot_equity_usdc", "Current account equity in USDC")

# Orders
order_latency_ms = Histogram(
    "hlbot_order_latency_ms",
    "Order submission latency in ms",
    buckets=[5, 10, 25, 50, 100, 250, 500, 1000],
)
fill_rate = Gauge("hlbot_fill_rate", "Order fill rate")
orders_submitted = Counter("hlbot_orders_submitted_total", "Total orders submitted", ["strategy"])
orders_filled = Counter("hlbot_orders_filled_total", "Total orders filled", ["strategy"])

# Positions
open_positions_count = Gauge("hlbot_open_positions_count", "Number of open positions")

# WebSocket
ws_reconnect_count = Counter("hlbot_ws_reconnect_total", "WebSocket reconnection count")

# Risk
risk_rejections = Counter("hlbot_risk_rejections_total", "Orders rejected by risk manager", ["reason"])

# Tick
tick_duration_ms = Histogram(
    "hlbot_tick_duration_ms",
    "Main loop tick duration in ms",
    buckets=[1, 5, 10, 25, 50, 100, 250],
)


class MetricsServer:
    """Starts Prometheus HTTP metrics server."""

    def __init__(self, port: int = 9090) -> None:
        self._port = port
        self._started = False

    def start(self) -> None:
        if not self._started:
            start_http_server(self._port)
            self._started = True
            log.info("metrics_server_started", port=self._port)

    def record_tick(self, duration_ms: float) -> None:
        tick_duration_ms.observe(duration_ms)

    def update_pnl(self, total: float, per_strategy: dict[str, float]) -> None:
        total_pnl_usdc.set(total)
        for name, pnl in per_strategy.items():
            strategy_pnl_usdc.labels(strategy=name).set(pnl)

    def update_equity(self, value: float) -> None:
        equity_usdc.set(value)

    def update_positions(self, count: int) -> None:
        open_positions_count.set(count)

    def update_fill_rate(self, rate: float) -> None:
        fill_rate.set(rate)
