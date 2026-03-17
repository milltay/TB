"""Per-strategy and aggregate PnL tracking with SQLite persistence."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import structlog

from src.core.state_store import StateStore
from src.core.types import OrderResult, Side

log = structlog.get_logger()


class PnLTracker:
    """Tracks realized and unrealized PnL per strategy and in aggregate."""

    def __init__(self, state: StateStore, db_path: str = "pnl_history.db") -> None:
        self._state = state
        self._realized: dict[str, float] = {}  # strategy → realized PnL
        self._fills: dict[str, list] = {}  # strategy → fill history
        self._peak_equity: float = 0.0
        self._max_drawdown: float = 0.0

        # SQLite for historical persistence
        self._db = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS pnl_snapshots (
                timestamp REAL,
                strategy TEXT,
                realized_pnl REAL,
                unrealized_pnl REAL,
                equity REAL
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS fills (
                timestamp REAL,
                strategy TEXT,
                asset TEXT,
                side TEXT,
                price REAL,
                size REAL,
                pnl REAL
            )
        """)
        self._db.commit()

    def record_fill(self, result: OrderResult) -> None:
        strategy = result.strategy_name or "unknown"
        self._realized.setdefault(strategy, 0)
        self._fills.setdefault(strategy, [])

        fill_record = {
            "timestamp": result.timestamp,
            "asset": result.asset,
            "side": result.side.value,
            "price": result.fill_price,
            "size": result.fill_size,
        }
        self._fills[strategy].append(fill_record)

        # Persist fill
        self._db.execute(
            "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?)",
            (result.timestamp, strategy, result.asset, result.side.value,
             result.fill_price, result.fill_size, 0),
        )
        self._db.commit()

    def update(self) -> None:
        """Snapshot current PnL state."""
        equity = self._state.get_equity()
        if equity > self._peak_equity:
            self._peak_equity = equity

        drawdown = self._peak_equity - equity if self._peak_equity > 0 else 0
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown

        now = time.time()
        for strategy, realized in self._realized.items():
            self._db.execute(
                "INSERT INTO pnl_snapshots VALUES (?, ?, ?, ?, ?)",
                (now, strategy, realized, 0, equity),
            )
        self._db.commit()

    def get_strategy_pnl(self, strategy: str) -> dict:
        return {
            "realized": self._realized.get(strategy, 0),
            "fill_count": len(self._fills.get(strategy, [])),
        }

    def get_aggregate_pnl(self) -> dict:
        total_realized = sum(self._realized.values())
        total_unrealized = sum(
            p.unrealized_pnl for p in self._state.get_all_positions()
        )
        return {
            "realized": total_realized,
            "unrealized": total_unrealized,
            "total": total_realized + total_unrealized,
            "max_drawdown": self._max_drawdown,
            "equity": self._state.get_equity(),
        }

    def get_win_rate(self, strategy: str | None = None) -> float:
        """Compute win rate from fill history (simplified)."""
        fills = []
        if strategy:
            fills = self._fills.get(strategy, [])
        else:
            for f_list in self._fills.values():
                fills.extend(f_list)
        if len(fills) < 2:
            return 0.0
        # Simplified: count positive PnL round trips
        return 0.0  # Requires proper round-trip tracking

    def close(self) -> None:
        self._db.close()
