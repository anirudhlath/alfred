"""Telemetry decorators for frictionless instrumentation.

These decorators wrap functions to automatically record latency, token usage,
and event bus metrics. Data is buffered locally and can be flushed to
OpenTelemetry spans and/or the research vault collector.
"""

from __future__ import annotations

import functools
import inspect
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# In-memory telemetry buffer (not thread-safe; in production, this flushes to Redis/OTel)
_telemetry_buffer: list[dict[str, Any]] = []


def get_telemetry_buffer() -> list[dict[str, Any]]:
    """Access the in-memory telemetry buffer. Primarily for testing."""
    return _telemetry_buffer


def clear_telemetry_buffer() -> None:
    """Clear the buffer. Primarily for testing."""
    _telemetry_buffer.clear()


def _record(entry: dict[str, Any]) -> None:
    """Record a telemetry entry to the buffer."""
    entry.setdefault("timestamp", datetime.now(UTC).isoformat())
    _telemetry_buffer.append(entry)


def track_latency(category: str) -> Callable[..., Any]:
    """Decorator to track function execution latency in milliseconds."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                result = await fn(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                _record(
                    {
                        "metric_type": "latency",
                        "category": category,
                        "function": fn.__name__,
                        "value": duration_ms,
                        "unit": "ms",
                    }
                )
                return result

            return async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                result = fn(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                _record(
                    {
                        "metric_type": "latency",
                        "category": category,
                        "function": fn.__name__,
                        "value": duration_ms,
                        "unit": "ms",
                    }
                )
                return result

            return sync_wrapper

    return decorator


def track_tokens(model: str) -> Callable[..., Any]:
    """Decorator to track LLM/SLM token usage.

    The decorated function must return a dict containing at least:
    - prompt_tokens: int
    - completion_tokens: int
    - total_tokens: int (optional, computed if missing)
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            result = await fn(*args, **kwargs)
            duration_ms = (time.perf_counter() - start) * 1000

            if isinstance(result, dict):
                _record(
                    {
                        "metric_type": "tokens",
                        "category": "tokens",
                        "model": model,
                        "function": fn.__name__,
                        "prompt_tokens": result.get("prompt_tokens", 0),
                        "completion_tokens": result.get("completion_tokens", 0),
                        "total_tokens": result.get(
                            "total_tokens",
                            result.get("prompt_tokens", 0) + result.get("completion_tokens", 0),
                        ),
                        "inference_ms": duration_ms,
                        "unit": "tokens",
                    }
                )
            return result

        return wrapper

    return decorator


def track_event(bus: str) -> Callable[..., Any]:
    """Decorator to track event bus publish/subscribe metrics."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            result = await fn(*args, **kwargs)
            duration_ms = (time.perf_counter() - start) * 1000
            _record(
                {
                    "metric_type": "event_throughput",
                    "category": "event_throughput",
                    "bus": bus,
                    "function": fn.__name__,
                    "value": duration_ms,
                    "unit": "ms",
                }
            )
            return result

        return wrapper

    return decorator
