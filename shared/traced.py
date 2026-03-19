"""@traced decorator — creates OpenTelemetry spans for function calls."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, overload

from opentelemetry import trace


@overload
def traced(fn: Any) -> Any: ...


@overload
def traced(
    *,
    name: str | None = None,
) -> Any: ...


def traced(
    fn: Any | None = None,
    *,
    name: str | None = None,
) -> Any:
    """Decorator that wraps a function in an OpenTelemetry span.

    Supports both sync and async functions.
    Supports both @traced and @traced(name="custom.name").
    """

    def decorator(f: Any) -> Any:
        span_name = name or f"{f.__module__}.{f.__qualname__}"
        tracer = trace.get_tracer(f.__module__)

        if asyncio.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(span_name) as span:
                    try:
                        result = await f(*args, **kwargs)
                        return result
                    except Exception as exc:
                        span.set_status(trace.StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return async_wrapper
        else:

            @functools.wraps(f)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(span_name) as span:
                    try:
                        result = f(*args, **kwargs)
                        return result
                    except Exception as exc:
                        span.set_status(trace.StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return sync_wrapper

    if fn is not None:
        return decorator(fn)
    return decorator
