"""@traced decorator — creates OpenTelemetry spans for function calls."""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, overload

from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

# Used by the plain @traced(*) overload which cannot use PEP 695 syntax
_P = ParamSpec("_P")
_R = TypeVar("_R")


@overload
def traced[**P, R](
    fn: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]: ...


@overload
def traced[**P, R](fn: Callable[P, R]) -> Callable[P, R]: ...


@overload
def traced(
    *,
    name: str | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]: ...


def traced[**P](
    fn: Callable[P, Any] | None = None,
    *,
    name: str | None = None,
) -> Any:
    """Decorator that wraps a function in an OpenTelemetry span.

    Supports both sync and async functions.
    Supports both @traced and @traced(name="custom.name").
    """

    def decorator(f: Callable[P, Any]) -> Callable[P, Any]:
        span_name = name or f"{f.__module__}.{f.__qualname__}"
        tracer = trace.get_tracer(f.__module__)

        if asyncio.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
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
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
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
