"""Shared types for the trading bot."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"
    IOC = "ioc"
    ALO = "alo"


class OrderStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"


@dataclass
class OrderIntent:
    strategy_name: str
    asset: str
    side: Side
    size: float
    price: float
    order_type: OrderType = OrderType.LIMIT
    reduce_only: bool = False
    cloid: str | None = None


@dataclass
class OrderResult:
    cloid: str | None
    oid: int | None
    status: OrderStatus
    fill_price: float | None = None
    fill_size: float | None = None
    timestamp: float = field(default_factory=time.time)
    asset: str = ""
    side: Side = Side.BUY
    strategy_name: str = ""


@dataclass
class Position:
    asset: str
    size: float  # Positive = long, negative = short
    entry_price: float
    unrealized_pnl: float = 0.0
    leverage: int = 1
    liquidation_price: float | None = None


@dataclass
class BookLevel:
    price: float
    size: float


@dataclass
class BookSnapshot:
    asset: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    mid_price: float
    spread: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class FundingSnapshot:
    asset: str
    current_rate: float
    predicted_rate: float
    next_funding_time: float | None = None
    open_interest: float = 0.0


@dataclass
class Candle:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
