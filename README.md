# Hyperliquid Multi-Strategy Trading Bot

Production-grade, modular trading bot for [Hyperliquid](https://hyperliquid.xyz) on-chain perpetuals DEX.

## Features

- **Multi-strategy**: Market Making, Funding Rate Arbitrage, Momentum, Liquidation Sniping
- **Shared risk management**: Portfolio-level exposure limits, per-asset caps, correlation checks
- **Circuit breaker**: Automatic kill switch on configurable drawdown threshold
- **Real-time data**: WebSocket-based market data with automatic reconnection
- **Observability**: Prometheus metrics, Discord alerts, SQLite PnL history
- **Dry-run mode**: Test strategies without submitting real orders

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Configure (edit config/global.yaml with your API wallet credentials)
cp config/global.yaml config/global.yaml.bak
# Edit config/global.yaml

# Run on testnet in dry-run mode
python -m src.main --dry-run --network testnet

# Run tests
pytest tests/ -v
```

## Configuration

All configuration is in YAML files under `config/`:

| File | Purpose |
|------|---------|
| `global.yaml` | API keys, risk limits, alert webhooks, enabled strategies |
| `market_making.yaml` | Market making parameters (spread, levels, inventory limits) |
| `funding_arb.yaml` | Funding rate arbitrage parameters |
| `momentum.yaml` | Momentum/trend following parameters |
| `liquidation.yaml` | Liquidation sniping parameters |

### API Wallet Setup

1. Go to https://app.hyperliquid.xyz/API
2. Generate an API wallet
3. Set `secret_key` to the API wallet's private key
4. Set `account_address` to your **main wallet** public key (not the API wallet)

## Architecture

```
main.py (orchestrator)
  ├── data/ (WebSocket → StateStore)
  ├── strategies/ (read StateStore → emit OrderIntents)
  ├── risk/ (filter OrderIntents)
  ├── execution/ (submit to Hyperliquid)
  └── observability/ (PnL, metrics, alerts)
```

**Tick loop** (100ms default):
1. All strategies produce `OrderIntent`s
2. Risk manager filters/rejects
3. Executor batches and submits
4. Fills routed back to originating strategy
5. PnL and metrics updated

## Capital Allocation (3,400 USDC)

| Strategy | Margin | Notes |
|----------|--------|-------|
| Market Making | ~1,200 USDC | 3x leverage, BTC + ETH |
| Funding Arb | ~900 USDC | 3 positions × 300 each |
| Reserve | ~1,300 USDC | Margin buffer + drawdown cushion |
| Momentum | Disabled | Enable after MM + Funding proven |
| Liquidation | Disabled | Enable after MM + Funding proven |

Max total exposure: 2,500 USDC notional (enforced by risk manager).

## Monitoring

```bash
# Prometheus metrics on localhost:9090
# CLI dashboard
python scripts/monitor.py

# Backtest
python scripts/backtest.py
```

## Important Caveats

- **This bot trades real money.** Start on testnet. Run for 24h+ before mainnet.
- **Funding arb is single-leg** (not delta-neutral) unless a spot hedge is added on another venue.
- **Market making carries inventory risk** — sudden moves can cause losses exceeding spread profits.
- **Rate limits are volume-dependent** — low-volume accounts may get throttled faster.
- **Requires reliable network connectivity.** Use systemd or Docker for auto-restart on a VPS.
- The `--dry-run` flag logs all orders without submitting them.

## Testing

```bash
pytest tests/ -v --cov=src
```

## Deployment

Recommended: Tokyo-region VPS for lowest latency to Hyperliquid.

```bash
# systemd service example
[Unit]
Description=Hyperliquid Trading Bot
After=network.target

[Service]
ExecStart=/usr/bin/python -m src.main --config-dir /opt/hlbot/config
WorkingDirectory=/opt/hlbot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
