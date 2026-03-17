"""Drawdown-based circuit breaker / kill switch."""

from __future__ import annotations

import structlog

from src.core.state_store import StateStore

log = structlog.get_logger()


class CircuitBreaker:
    """Triggers when account drawdown exceeds configured threshold.

    Once triggered, requires manual restart.
    """

    def __init__(self, starting_balance: float, max_drawdown_pct: float) -> None:
        self._starting_balance = starting_balance
        self._max_drawdown_pct = max_drawdown_pct
        self._max_loss = starting_balance * (max_drawdown_pct / 100)
        self._triggered = False
        self._peak_equity = starting_balance

    def check(self, current_equity: float) -> bool:
        """Check if circuit breaker should trigger. Returns True if triggered."""
        if self._triggered:
            return True

        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        drawdown = self._starting_balance - current_equity
        drawdown_pct = (drawdown / self._starting_balance) * 100

        if drawdown >= self._max_loss:
            self._triggered = True
            log.critical(
                "circuit_breaker_triggered",
                drawdown_usdc=round(drawdown, 2),
                drawdown_pct=round(drawdown_pct, 2),
                equity=round(current_equity, 2),
                threshold_pct=self._max_drawdown_pct,
            )
            return True

        return False

    def is_triggered(self) -> bool:
        return self._triggered

    def get_drawdown_pct(self, current_equity: float) -> float:
        drawdown = self._starting_balance - current_equity
        return (drawdown / self._starting_balance) * 100 if self._starting_balance else 0

    def reset(self) -> None:
        """Manual reset after investigation."""
        self._triggered = False
        log.warning("circuit_breaker_reset")
