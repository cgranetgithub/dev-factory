"""
Retry decorator for transient failures (network, timeout).

Usage::

    from devfactory.models.retry import with_retry

    @with_retry(max_attempts=3, delay=10.0)
    def flaky_call():
        ...
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

# Base set of retriable exception types (always available)
_RETRYABLE: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)

# Extend with httpx exceptions if available
try:
    import httpx

    _RETRYABLE = _RETRYABLE + (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.RemoteProtocolError,
    )
except ImportError:
    pass

RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = _RETRYABLE


def with_retry(
    max_attempts: int = 3,
    delay: float = 5.0,
    backoff: float = 2.0,
) -> Callable[[F], F]:
    """
    Decorator that retries a function on transient network/timeout errors.

    Args:
        max_attempts: Total number of attempts (1 = no retry).
        delay:        Initial wait between retries in seconds.
        backoff:      Multiply delay by this factor on each retry.

    Raises:
        RuntimeError: Wrapping the last exception after all attempts fail.
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):  # type: ignore[return]
            last_exc: Exception | None = None
            current_delay = delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except RETRYABLE_EXCEPTIONS as exc:
                    last_exc = exc
                    if attempt >= max_attempts:
                        break
                    logger.warning(
                        f"[retry] {fn.__qualname__} failed "
                        f"(attempt {attempt}/{max_attempts}): {exc}. "
                        f"Retrying in {current_delay:.0f}s…"
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff

            raise RuntimeError(
                f"{fn.__qualname__} failed after {max_attempts} attempt(s)"
            ) from last_exc

        return wrapper  # type: ignore[return-value]

    return decorator  # type: ignore[return-value]
