"""Pydantic configuration models for the trading bot."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class RiskConfig(BaseModel):
    max_total_exposure_usdc: float = 2500
    max_per_asset_exposure_usdc: float = 800
    max_drawdown_pct: float = 5.0
    max_open_orders: int = 20
    correlation_check: bool = True


class ExecutionConfig(BaseModel):
    default_leverage: int = 3
    batch_size: int = 10
    retry_attempts: int = 3
    retry_backoff_base_ms: int = 200


class AlertsConfig(BaseModel):
    discord_webhook_url: str = ""
    alert_on_fill: bool = True
    alert_on_error: bool = True
    alert_on_drawdown_pct: float = 3.0


class MetricsConfig(BaseModel):
    prometheus_port: int = 9090


class StrategiesConfig(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["market_making", "funding_arb"])


class GlobalConfig(BaseModel):
    account_address: str = ""
    secret_key: str = ""
    network: str = "testnet"
    starting_balance_usdc: float = 3400
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    dry_run: bool = False


class MarketMakingConfig(BaseModel):
    assets: list[str] = Field(default_factory=lambda: ["BTC", "ETH"])
    spread_bps: float = 3
    order_size_usdc: float = 50
    num_levels: int = 3
    level_spacing_bps: float = 2
    inventory_skew_factor: float = 0.5
    max_inventory_usdc: float = 400
    refresh_interval_ms: int = 500
    use_alo: bool = True


class FundingArbConfig(BaseModel):
    min_funding_rate_annualized: float = 15.0
    position_size_usdc: float = 300
    max_concurrent_positions: int = 3
    check_interval_seconds: int = 60
    exit_threshold_annualized: float = 5.0
    assets_whitelist: list[str] = Field(default_factory=list)
    delta_neutral: bool = False


class MomentumConfig(BaseModel):
    assets: list[str] = Field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    lookback_candles: int = 20
    candle_interval: str = "5m"
    entry_threshold_pct: float = 0.5
    position_size_usdc: float = 200
    take_profit_pct: float = 1.5
    stop_loss_pct: float = 0.8
    max_concurrent_positions: int = 2
    cooldown_after_stop_seconds: int = 300
    use_vwap_confirmation: bool = True


class LiquidationConfig(BaseModel):
    scan_interval_seconds: int = 10
    min_liquidation_size_usdc: float = 1000
    position_size_usdc: float = 150
    max_hold_seconds: int = 60
    profit_target_bps: float = 20
    assets_whitelist: list[str] = Field(default_factory=list)


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_global_config(config_dir: Path | str = "config") -> GlobalConfig:
    config_dir = Path(config_dir)
    data = load_yaml(config_dir / "global.yaml")
    return GlobalConfig(**data)


def load_strategy_config(strategy_name: str, config_dir: Path | str = "config") -> dict[str, Any]:
    config_dir = Path(config_dir)
    path = config_dir / f"{strategy_name}.yaml"
    if path.exists():
        return load_yaml(path)
    return {}
