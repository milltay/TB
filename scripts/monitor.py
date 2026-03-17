"""CLI dashboard for live monitoring of the trading bot."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))


def fetch_metrics(port: int = 9090) -> str:
    """Fetch Prometheus metrics from the bot."""
    try:
        with urlopen(f"http://localhost:{port}/metrics", timeout=5) as resp:
            return resp.read().decode()
    except Exception as e:
        return f"Error fetching metrics: {e}"


def parse_metric(text: str, name: str) -> str:
    for line in text.split("\n"):
        if line.startswith(name + " ") or line.startswith(name + "{"):
            return line.split(" ")[-1]
    return "N/A"


def display_dashboard(metrics_text: str) -> None:
    """Print a formatted dashboard."""
    print("\033[2J\033[H")  # Clear screen
    print("=" * 60)
    print("  HYPERLIQUID TRADING BOT - LIVE MONITOR")
    print("=" * 60)
    print()

    equity = parse_metric(metrics_text, "hlbot_equity_usdc")
    total_pnl = parse_metric(metrics_text, "hlbot_total_pnl_usdc")
    positions = parse_metric(metrics_text, "hlbot_open_positions_count")
    fill_rate = parse_metric(metrics_text, "hlbot_fill_rate")
    ws_reconnects = parse_metric(metrics_text, "hlbot_ws_reconnect_total")

    print(f"  Equity:          ${equity}")
    print(f"  Total PnL:       ${total_pnl}")
    print(f"  Open Positions:  {positions}")
    print(f"  Fill Rate:       {fill_rate}")
    print(f"  WS Reconnects:   {ws_reconnects}")
    print()
    print("-" * 60)
    print(f"  Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("  Press Ctrl+C to exit")


def main():
    port = 9090
    if len(sys.argv) > 1:
        port = int(sys.argv[1])

    print(f"Monitoring bot metrics on port {port}...")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            metrics = fetch_metrics(port)
            display_dashboard(metrics)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
