"""Retry logic with exponential backoff for failed submissions."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

import structlog

log = structlog.get_logger()


async def retry_with_backoff(
    fn: Callable[[], Any],
    max_attempts: int = 3,
    base_ms: int = 200,
) -> Any:
    """Retry an async or sync function with exponential backoff.

    Args:
        fn: Function to call (can be sync or async).
        max_attempts: Maximum number of attempts.
        base_ms: Base backoff in milliseconds (doubles each attempt).

    Returns:
        The result of the function call.

    Raises:
        The last exception if all attempts fail.
    """
    last_error = None

    for attempt in range(max_attempts):
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                backoff_s = (base_ms * (2 ** attempt)) / 1000
                log.warning(
                    "retry_attempt",
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    backoff_ms=base_ms * (2 ** attempt),
                    error=str(e),
                )
                await asyncio.sleep(backoff_s)

    raise last_error
