"""@traced decorator — creates OpenTelemetry spans for function calls."""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, overload

from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

_P = ParamSpec("_P")
_R = TypeVar("_R")


@overload
def traced(
    fn: Callable[_P, Coroutine[Any, Any, _R]],
) -> Callable[_P, Coroutine[Any, Any, _R]]: ...


@overload
def traced(fn: Callable[_P, _R]) -> Callable[_P, _R]: ...


@overload
def traced(
    *,
    name: str | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]: ...


def traced(
    fn: Callable[_P, Any] | None = None,
    *,
    name: str | None = None,
) -> Any:
    """Decorator that wraps a function in an OpenTelemetry span.

    Supports both sync and async functions.
    Supports both @traced and @traced(name="custom.name").
    """

    def decorator(f: Callable[_P, Any]) -> Callable[_P, Any]:
        span_name = name or f"{f.__module__}.{f.__qualname__}"
        tracer = trace.get_tracer(f.__module__)

        if asyncio.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Any:
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
            def sync_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Any:
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
