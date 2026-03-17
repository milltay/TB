"""Lightweight async pub/sub event bus."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

import structlog

log = structlog.get_logger()

Callback = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """Simple publish/subscribe for internal bot events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callback]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callback) -> None:
        self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callback) -> None:
        subs = self._subscribers.get(event_type, [])
        if callback in subs:
            subs.remove(callback)

    async def publish(self, event_type: str, **kwargs: Any) -> None:
        for callback in self._subscribers.get(event_type, []):
            try:
                await callback(**kwargs)
            except Exception:
                log.exception("event_handler_error", event_type=event_type)

    def publish_nowait(self, event_type: str, **kwargs: Any) -> None:
        for callback in self._subscribers.get(event_type, []):
            asyncio.ensure_future(self._safe_call(callback, **kwargs))

    @staticmethod
    async def _safe_call(callback: Callback, **kwargs: Any) -> None:
        try:
            await callback(**kwargs)
        except Exception:
            log.exception("event_handler_error")
