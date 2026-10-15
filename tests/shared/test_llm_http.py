"""The retrying transport under every OpenAI-compatible model (spec 6b, "Model requests")."""

from __future__ import annotations

import gzip
import json
import logging
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import TYPE_CHECKING

import httpx2
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.settings import ModelSettings

import shared.llm_http as llm_http
from shared.agents import resolve_model
from shared.llm_http import (
    RETRY_STATUSES,
    RetryingTransport,
    RetryPolicy,
    parse_retry_after,
)
from shared.llm_roles import ROLE_HEADER

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

URL = "http://model.test/v1/chat/completions"
BODY = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

Step = httpx2.Response | Exception


class Harness:
    """A scripted upstream, with a fake sleep, clock and rng that record what they saw."""

    def __init__(self, steps: list[Step], *, clock_step: float = 0.0, rng_value: float = 0.25):
        self._steps: Iterator[Step] = iter(steps)
        self.requests: list[httpx2.Request] = []
        self.bodies: list[bytes] = []
        self.waits: list[float] = []
        self.rng_calls: list[tuple[float, float]] = []
        self._now = 0.0
        self._clock_step = clock_step
        self._rng_value = rng_value

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.bodies.append(request.content)
        self._now += self._clock_step  # each attempt takes clock_step seconds
        step = next(self._steps)
        if isinstance(step, Exception):
            raise step
        return step

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self._now += seconds

    def clock(self) -> float:
        return self._now

    def rng(self, low: float, high: float) -> float:
        self.rng_calls.append((low, high))
        return self._rng_value

    def transport(self, policy: RetryPolicy | None = None) -> RetryingTransport:
        return RetryingTransport(
            policy or RetryPolicy(),
            httpx2.MockTransport(self.handler),
            sleep=self.sleep,
            clock=self.clock,
            rng=self.rng,
        )


async def _post(transport: RetryingTransport, role: str = "system2") -> httpx2.Response:
    async with httpx2.AsyncClient(transport=transport) as client:
        response = await client.post(URL, json=BODY, headers={ROLE_HEADER: role})
        await response.aread()
        return response


def test_the_retried_statuses_and_policy_are_the_spec_numbers() -> None:
    assert frozenset({408, 409, 429, 500, 502, 503, 504, 529}) == RETRY_STATUSES
    assert RetryPolicy() == RetryPolicy(attempts=3, budget_s=30.0, base_s=0.5, cap_s=8.0)


@pytest.mark.asyncio
async def test_a_503_then_200_retries_once_with_the_jittered_wait_and_the_same_body() -> None:
    h = Harness([httpx2.Response(503, text="busy"), httpx2.Response(200, json={"ok": True})])

    response = await _post(h.transport())

    assert response.status_code == 200
    assert h.waits == [0.25]
    assert h.rng_calls[0] == (0, 0.5)  # uniform(0, min(8, 0.5 * 2**0))
    assert len(h.requests) == 2
    assert h.bodies[0] == h.bodies[1]
    assert json.loads(h.bodies[1]) == BODY


@pytest.mark.asyncio
async def test_the_backoff_doubles_up_to_the_cap() -> None:
    h = Harness([httpx2.Response(500), httpx2.Response(500), httpx2.Response(200)])
    await _post(h.transport(RetryPolicy(attempts=3, base_s=5.0, cap_s=8.0)))
    assert h.rng_calls[:2] == [(0, 5.0), (0, 8.0)]


@pytest.mark.asyncio
async def test_a_429_with_retry_after_seconds_waits_that_long() -> None:
    h = Harness([httpx2.Response(429, headers={"retry-after": "2"}), httpx2.Response(200)])
    response = await _post(h.transport())
    assert response.status_code == 200
    assert h.waits == [2.0]


@pytest.mark.asyncio
async def test_a_429_with_retry_after_ms_waits_that_long() -> None:
    h = Harness([httpx2.Response(429, headers={"retry-after-ms": "1500"}), httpx2.Response(200)])
    response = await _post(h.transport())
    assert response.status_code == 200
    assert h.waits == [1.5]


def test_retry_after_as_an_http_date_is_seconds_from_now() -> None:
    when = datetime.now(UTC) + timedelta(seconds=3)
    wait = parse_retry_after(httpx2.Headers({"retry-after": format_datetime(when, usegmt=True)}))
    assert wait == pytest.approx(3, abs=1)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, None),
        ({"retry-after": "soon"}, None),
        ({"retry-after": "-1"}, None),
        ({"retry-after-ms": "250", "retry-after": "9"}, 0.25),
        ({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}, 0.0),  # a past date waits nothing
    ],
)
def test_parse_retry_after_edge_cases(headers: dict[str, str], expected: float | None) -> None:
    assert parse_retry_after(httpx2.Headers(headers)) == expected


@pytest.mark.asyncio
async def test_a_retry_after_beyond_the_budget_surfaces_the_429_at_once() -> None:
    h = Harness([httpx2.Response(429, headers={"retry-after": "120"}, text="slow down")])
    response = await _post(h.transport())
    assert response.status_code == 429
    assert response.text == "slow down"
    assert h.waits == []
    assert len(h.requests) == 1


@pytest.mark.asyncio
async def test_three_500s_return_the_third_response_with_a_readable_body() -> None:
    h = Harness([httpx2.Response(500, text=f"boom {n}") for n in (1, 2, 3)])
    response = await _post(h.transport())
    assert response.status_code == 500
    assert response.text == "boom 3"
    assert len(h.requests) == 3
    assert len(h.waits) == 2


@pytest.mark.asyncio
async def test_a_returned_retryable_response_keeps_its_content_encoding_intact() -> None:
    gzipped = gzip.compress(b'{"error": "overloaded"}')
    steps: list[Step] = [
        httpx2.Response(503, headers={"content-encoding": "gzip"}, content=gzipped)
        for _ in range(3)
    ]
    h = Harness(steps)
    response = await _post(h.transport())
    assert response.status_code == 503
    assert response.json() == {"error": "overloaded"}


@pytest.mark.asyncio
async def test_a_400_is_not_retried() -> None:
    h = Harness([httpx2.Response(400, text="bad")])
    response = await _post(h.transport())
    assert response.status_code == 400
    assert response.text == "bad"
    assert len(h.requests) == 1
    assert h.waits == []


@pytest.mark.asyncio
async def test_connect_errors_are_retried_until_a_response() -> None:
    h = Harness(
        [httpx2.ConnectError("refused"), httpx2.ConnectError("refused"), httpx2.Response(200)]
    )
    response = await _post(h.transport())
    assert response.status_code == 200
    assert len(h.requests) == 3


@pytest.mark.asyncio
async def test_three_connect_errors_reraise_the_last_one() -> None:
    h = Harness([httpx2.ConnectError(f"refused {n}") for n in (1, 2, 3)])
    with pytest.raises(httpx2.ConnectError, match="refused 3"):
        await _post(h.transport())
    assert len(h.requests) == 3


@pytest.mark.parametrize(
    "exc",
    [
        httpx2.ConnectTimeout("t"),
        httpx2.ReadTimeout("t"),
        httpx2.WriteTimeout("t"),
        httpx2.PoolTimeout("t"),
        httpx2.RemoteProtocolError("dropped"),
        httpx2.ReadError("reset"),
    ],
)
@pytest.mark.asyncio
async def test_each_no_usable_response_error_is_retried(exc: Exception) -> None:
    h = Harness([exc, httpx2.Response(200)])
    response = await _post(h.transport())
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_other_errors_are_not_retried() -> None:
    h = Harness([httpx2.UnsupportedProtocol("ftp?")])
    with pytest.raises(httpx2.UnsupportedProtocol):
        await _post(h.transport())
    assert len(h.requests) == 1


@pytest.mark.asyncio
async def test_a_wait_that_would_cross_the_budget_stops_early() -> None:
    # Each attempt takes 25 s; a 5.5 s wait after the first would end at 30.5 s > 30 s.
    h = Harness([httpx2.Response(503), httpx2.Response(200)], clock_step=25.0, rng_value=5.5)
    response = await _post(h.transport(RetryPolicy(base_s=8.0)))
    assert response.status_code == 503
    assert len(h.requests) == 1
    assert h.waits == []


@pytest.mark.asyncio
async def test_each_retry_logs_one_warning_naming_the_role(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness([httpx2.Response(503), httpx2.ConnectError("refused"), httpx2.Response(200)])
    with caplog.at_level(logging.WARNING, logger="shared.llm_http"):
        await _post(h.transport(), role="librarian")
    records = [r for r in caplog.records if r.name == "shared.llm_http"]
    assert len(records) == 2
    assert all(r.levelno == logging.WARNING for r in records)
    assert all("librarian" in r.getMessage() for r in records)
    assert "503" in records[0].getMessage() and "attempt 1" in records[0].getMessage()
    assert "ConnectError" in records[1].getMessage() and "attempt 2" in records[1].getMessage()


@pytest.mark.asyncio
async def test_each_retry_adds_an_llm_retry_span_event() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    h = Harness([httpx2.Response(503), httpx2.Response(200)])
    with provider.get_tracer("test").start_as_current_span("turn"):
        await _post(h.transport())
    (span,) = exporter.get_finished_spans()
    (event,) = [e for e in span.events if e.name == "llm.retry"]
    assert event.attributes is not None
    assert event.attributes["llm.role"] == "system2"
    assert event.attributes["llm.retry.attempt"] == 1
    assert event.attributes["http.response.status_code"] == 503
    assert event.attributes["llm.retry.wait_s"] == 0.25


@pytest.fixture
def upstream_429(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Every model request answered 429 with a Retry-After far past the budget."""
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(
            429, headers={"retry-after": "999"}, json={"error": {"message": "rate limited"}}
        )

    monkeypatch.setattr(llm_http, "_base_transport", lambda: httpx2.MockTransport(handler))
    return lambda: calls


@pytest.mark.asyncio
async def test_exhausted_retries_surface_as_model_http_error_with_the_real_status(
    monkeypatch: pytest.MonkeyPatch, upstream_429: Callable[[], int]
) -> None:
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.test:8000/v1")
    model = resolve_model("SYSTEM2_MODEL", "vllm:probe")
    assert model is not None
    agent = Agent(model, model_settings=ModelSettings(extra_headers={ROLE_HEADER: "system2"}))

    with override_allow_model_requests(True), pytest.raises(ModelHTTPError) as caught:
        await agent.run("hi")

    assert caught.value.status_code == 429
    assert upstream_429() == 1  # one request: the SDK does not retry, nor did the transport
