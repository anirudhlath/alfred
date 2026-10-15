"""The one retry layer under every OpenAI-compatible model request (spec 6b, "Model requests").

The OpenAI SDK client is built with ``max_retries=0`` and this transport retries instead, so
the two never both retry. Retries are bounded by attempts *and* a time budget, wait with full
jitter or the server's ``Retry-After``, and log every retry.

When retries run out on a status, the last *response* is returned, not an exception: the SDK
then raises its normal ``APIStatusError`` and Pydantic AI a ``ModelHTTPError`` carrying the
real status and headers, which the turn layer classifies. Pydantic AI's own
``AsyncHTTPX2TenacityTransport`` raises from inside the transport instead, which the SDK
reports as a connection error, losing the status.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Final

import httpx2
from opentelemetry import trace
from pydantic_ai._http import (
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
    ConnectPoolTimeoutCap,
)
from pydantic_ai.models import get_user_agent
from tenacity import AsyncRetrying, RetryCallState

from shared.llm_roles import ROLE_HEADER

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

RETRY_STATUSES: Final[frozenset[int]] = frozenset({408, 409, 429, 500, 502, 503, 504, 529})

# Transport errors that mean no usable response arrived, so a second try can succeed.
RETRY_EXCEPTIONS: Final[tuple[type[httpx2.TransportError], ...]] = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.ReadTimeout,
    httpx2.WriteTimeout,
    httpx2.PoolTimeout,
    httpx2.RemoteProtocolError,
    httpx2.ReadError,
)

UNKNOWN_ROLE: Final = "unknown"


@dataclass(frozen=True)
class RetryPolicy:
    """At most *attempts* tries, all retries done within *budget_s* of the first."""

    attempts: int = 3
    budget_s: float = 30.0
    base_s: float = 0.5
    cap_s: float = 8.0


DEFAULT_POLICY: Final = RetryPolicy()


def parse_retry_after(headers: httpx2.Headers) -> float | None:
    """The server's requested wait in seconds, or None when it names none usable.

    ``retry-after-ms`` wins over ``retry-after``, which is seconds or an HTTP date.
    """
    millis = _non_negative(headers.get("retry-after-ms"))
    if millis is not None:
        return millis / 1000
    value = headers.get("retry-after")
    if value is None:
        return None
    seconds = _non_negative(value)
    if seconds is not None:
        return seconds
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _non_negative(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


async def _drained(response: httpx2.Response, request: httpx2.Request) -> httpx2.Response:
    """*response* read in full and closed, re-wrapped so its body can still be read.

    The raw bytes are kept, not the decoded ones, so the ``content-encoding`` header stays
    true for whoever decodes the returned response. The stream is read directly rather than
    through ``aiter_raw``, which refuses a response built in memory (already read).
    """
    try:
        stream = response.stream
        if not isinstance(stream, httpx2.AsyncByteStream):
            raise TypeError(f"an async transport returned a {type(stream).__name__} stream")
        raw = b"".join([chunk async for chunk in stream])
    finally:
        await response.aclose()
    return httpx2.Response(
        response.status_code,
        headers=response.headers,
        stream=httpx2.ByteStream(raw),
        request=request,
        extensions=response.extensions,
    )


class RetryingTransport(httpx2.AsyncBaseTransport):
    """Retries a request on transient transport errors and statuses, within *policy*."""

    def __init__(
        self,
        policy: RetryPolicy = DEFAULT_POLICY,
        wrapped: httpx2.AsyncBaseTransport | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._policy = policy
        self._wrapped = wrapped if wrapped is not None else httpx2.AsyncHTTPTransport()
        self._sleep = sleep
        self._clock = clock
        self._rng = rng

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        policy = self._policy
        started = self._clock()
        role = request.headers.get(ROLE_HEADER, UNKNOWN_ROLE)

        def should_retry(state: RetryCallState) -> bool:
            outcome = state.outcome
            if outcome is None:
                return False
            if outcome.failed:
                return isinstance(outcome.exception(), RETRY_EXCEPTIONS)
            response: httpx2.Response = outcome.result()
            return response.status_code in RETRY_STATUSES

        def wait(state: RetryCallState) -> float:
            outcome = state.outcome
            if outcome is not None and not outcome.failed:
                requested = parse_retry_after(outcome.result().headers)
                if requested is not None:
                    return requested
            ceiling = min(policy.cap_s, policy.base_s * 2 ** (state.attempt_number - 1))
            return self._rng(0, ceiling)

        def stop(state: RetryCallState) -> bool:
            # tenacity computes the wait before asking whether to stop.
            if state.attempt_number >= policy.attempts:
                return True
            elapsed = self._clock() - started
            return elapsed + state.upcoming_sleep > policy.budget_s

        def before_sleep(state: RetryCallState) -> None:
            outcome = state.outcome
            assert outcome is not None  # tenacity sleeps only after an attempt
            seconds = state.upcoming_sleep
            attributes: dict[str, str | int | float] = {
                "llm.role": role,
                "llm.retry.attempt": state.attempt_number,
                "llm.retry.wait_s": seconds,
            }
            if outcome.failed:
                reason = type(outcome.exception()).__name__
                attributes["exception.type"] = reason
            else:
                status = outcome.result().status_code
                reason = str(status)
                attributes["http.response.status_code"] = status
            logger.warning(
                "LLM request retry: role %s, attempt %d of %d failed with %s; waiting %.2fs",
                role,
                state.attempt_number,
                policy.attempts,
                reason,
                seconds,
            )
            trace.get_current_span().add_event("llm.retry", attributes)

        def give_up(state: RetryCallState) -> httpx2.Response:
            # The last response for a status; re-raises the last exception for an error.
            assert state.outcome is not None
            response: httpx2.Response = state.outcome.result()
            return response

        retrying = AsyncRetrying(
            sleep=self._sleep,
            retry=should_retry,
            wait=wait,
            stop=stop,
            before_sleep=before_sleep,
            retry_error_callback=give_up,
        )
        return await retrying(self._attempt, request)

    async def _attempt(self, request: httpx2.Request) -> httpx2.Response:
        response = await self._wrapped.handle_async_request(request)
        if response.status_code in RETRY_STATUSES:
            return await _drained(response, request)
        return response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def _base_transport() -> httpx2.AsyncBaseTransport:
    """The real network transport, with Pydantic AI's default pool limits; tests replace it."""
    return httpx2.AsyncHTTPTransport(
        limits=httpx2.Limits(
            max_connections=DEFAULT_MAX_CONNECTIONS,
            max_keepalive_connections=DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
        )
    )


def retrying_http_client(policy: RetryPolicy = DEFAULT_POLICY) -> httpx2.AsyncClient:
    """An HTTP client like Pydantic AI's default one, retrying through `RetryingTransport`.

    Timeouts, pool limits, user agent and the connect/pool timeout cap match
    ``create_async_httpx2_client``; a request's own timeout still comes from
    ``ModelSettings.timeout``.
    """
    timeout = httpx2.Timeout(DEFAULT_HTTP_TIMEOUT, connect=5)
    return httpx2.AsyncClient(
        timeout=timeout,
        transport=RetryingTransport(policy, _base_transport()),
        headers={"User-Agent": get_user_agent()},
        event_hooks={
            "request": [ConnectPoolTimeoutCap(connect=timeout.connect, pool=timeout.pool)]
        },
    )
