"""Momentum / trend following strategy with VWAP confirmation."""

from __future__ import annotations

import time
import uuid

import structlog

from src.core.config import MomentumConfig
from src.core.state_store import StateStore
from src.core.types import Candle, OrderIntent, OrderResult, OrderType, Side
from src.strategies.base import BaseStrategy

log = structlog.get_logger()

CANDLE_INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400,
}


class MomentumStrategy(BaseStrategy):
    """Enters positions on momentum breakouts with trailing stops.

    Uses rolling candle data and optional VWAP confirmation.
    """

    def __init__(self, config: dict, state: StateStore) -> None:
        super().__init__(config, state)
        self.cfg = MomentumConfig(**config)
        self.name = "momentum"
        # Rolling candles per asset
        self._candles: dict[str, list[Candle]] = {a: [] for a in self.cfg.assets}
        # Active positions: asset → {side, entry_price, stop_price, take_profit_price, entry_time}
        self._positions: dict[str, dict] = {}
        self._cooldowns: dict[str, float] = {}
        # VWAP tracking: asset → cumulative (price*volume, volume)
        self._vwap_cum: dict[str, tuple[float, float]] = {a: (0.0, 0.0) for a in self.cfg.assets}
        self._last_candle_close: dict[str, float] = {}
        self._candle_interval_s = CANDLE_INTERVAL_SECONDS.get(self.cfg.candle_interval, 300)

    async def on_tick(self) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        now = time.time()

        for asset in self.cfg.assets:
            mid = self.state.get_mid(asset)
            if not mid or mid <= 0:
                continue

            # Update candle data
            self._update_candle(asset, mid, now)

            # Check stop/TP for active positions
            if asset in self._positions:
                exit_intent = self._check_exit(asset, mid)
                if exit_intent:
                    intents.append(exit_intent)
                continue

            # Check cooldown
            if now < self._cooldowns.get(asset, 0):
                continue

            # Check if enough candles
            candles = self._candles[asset]
            if len(candles) < self.cfg.lookback_candles:
                continue

            # Check for entry signal
            if len(self._positions) >= self.cfg.max_concurrent_positions:
                continue

            intent = self._check_entry(asset, mid, candles)
            if intent:
                intents.append(intent)

        return intents

    def _update_candle(self, asset: str, mid: float, now: float) -> None:
        candles = self._candles[asset]
        last_close_time = self._last_candle_close.get(asset, 0)

        if now - last_close_time >= self._candle_interval_s:
            # Close current candle, start new one
            candles.append(Candle(
                timestamp=now,
                open=mid,
                high=mid,
                low=mid,
                close=mid,
                volume=0,
            ))
            self._last_candle_close[asset] = now
            # Keep only lookback + buffer
            max_candles = self.cfg.lookback_candles + 5
            if len(candles) > max_candles:
                self._candles[asset] = candles[-max_candles:]
        elif candles:
            # Update current candle
            c = candles[-1]
            c.high = max(c.high, mid)
            c.low = min(c.low, mid)
            c.close = mid

    def _check_entry(self, asset: str, mid: float, candles: list[Candle]) -> OrderIntent | None:
        lookback = self.cfg.lookback_candles
        old_close = candles[-lookback].close
        if old_close <= 0:
            return None

        momentum_pct = ((mid - old_close) / old_close) * 100

        if abs(momentum_pct) < self.cfg.entry_threshold_pct:
            return None

        # VWAP check
        if self.cfg.use_vwap_confirmation:
            pv, v = self._vwap_cum[asset]
            vwap = pv / v if v > 0 else mid
            if momentum_pct > 0 and mid < vwap:
                return None
            if momentum_pct < 0 and mid > vwap:
                return None

        side = Side.BUY if momentum_pct > 0 else Side.SELL
        size = self.cfg.position_size_usdc / mid

        # Calculate stop and TP
        if side == Side.BUY:
            stop_price = mid * (1 - self.cfg.stop_loss_pct / 100)
            tp_price = mid * (1 + self.cfg.take_profit_pct / 100)
        else:
            stop_price = mid * (1 + self.cfg.stop_loss_pct / 100)
            tp_price = mid * (1 - self.cfg.take_profit_pct / 100)

        self._positions[asset] = {
            "side": side,
            "entry_price": mid,
            "stop_price": stop_price,
            "take_profit_price": tp_price,
            "entry_time": time.time(),
        }

        log.info(
            "momentum_entry",
            asset=asset,
            side=side.value,
            momentum_pct=round(momentum_pct, 3),
            stop=round(stop_price, 2),
            tp=round(tp_price, 2),
        )

        return OrderIntent(
            strategy_name=self.name,
            asset=asset,
            side=side,
            size=size,
            price=mid,
            order_type=OrderType.IOC,
            cloid=f"mom-{asset}-{uuid.uuid4().hex[:8]}",
        )

    def _check_exit(self, asset: str, mid: float) -> OrderIntent | None:
        pos_info = self._positions[asset]
        side = pos_info["side"]
        stop = pos_info["stop_price"]
        tp = pos_info["take_profit_price"]

        triggered = False
        reason = ""

        if side == Side.BUY:
            # Trail stop up
            new_stop = mid * (1 - self.cfg.stop_loss_pct / 100)
            if new_stop > stop:
                pos_info["stop_price"] = new_stop
                stop = new_stop

            if mid <= stop:
                triggered = True
                reason = "stop_loss"
            elif mid >= tp:
                triggered = True
                reason = "take_profit"
        else:
            # Trail stop down
            new_stop = mid * (1 + self.cfg.stop_loss_pct / 100)
            if new_stop < stop:
                pos_info["stop_price"] = new_stop
                stop = new_stop

            if mid >= stop:
                triggered = True
                reason = "stop_loss"
            elif mid <= tp:
                triggered = True
                reason = "take_profit"

        if not triggered:
            return None

        pos = self.state.get_position(asset)
        size = abs(pos.size) if pos else self.cfg.position_size_usdc / mid
        exit_side = Side.SELL if side == Side.BUY else Side.BUY

        log.info("momentum_exit", asset=asset, reason=reason, price=mid)

        del self._positions[asset]

        if reason == "stop_loss":
            self._cooldowns[asset] = time.time() + self.cfg.cooldown_after_stop_seconds

        return OrderIntent(
            strategy_name=self.name,
            asset=asset,
            side=exit_side,
            size=size,
            price=mid,
            order_type=OrderType.IOC,
            reduce_only=True,
            cloid=f"mom-exit-{asset}-{uuid.uuid4().hex[:8]}",
        )

    def update_vwap(self, asset: str, price: float, volume: float) -> None:
        if asset in self._vwap_cum:
            pv, v = self._vwap_cum[asset]
            self._vwap_cum[asset] = (pv + price * volume, v + volume)

    async def on_fill(self, fill: OrderResult) -> None:
        log.info(
            "momentum_fill",
            asset=fill.asset,
            side=fill.side.value,
            price=fill.fill_price,
            size=fill.fill_size,
        )
