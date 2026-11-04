from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, NoReturn

import httpx
import pytest
from aiohttp import web

from evals.harness import proxy as proxy_module
from evals.harness.evidence import LlmCall
from evals.harness.judge import JUDGE_CONNECTIONS, make_judge_model
from evals.harness.proxy import MAX_UPSTREAM, ROLES_BY_HEADER, LlmProxy
from shared.llm_roles import APPROVAL, LIBRARIAN, LLM_ROLES, ROLE_HEADER, SYSTEM1, SYSTEM2

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from evals.harness.evidence import Role


COMPLETION = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "home_light_turn_on",
                            "arguments": '{"target": "Bedroom Lamp"}',
                        },
                    }
                ],
            }
        }
    ],
    "usage": {"prompt_tokens": 100, "completion_tokens": 7},
}


def upstream(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


@pytest.fixture
async def proxy():  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", transport=upstream(handle))
    await p.start()
    p.seen = seen  # type: ignore[attr-defined]
    yield p
    await p.stop()


async def test_records_a_system2_tool_call(proxy: LlmProxy) -> None:
    body = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "You are Alfred — personal butler and assistant to sir."}
        ],
        "tools": [
            {"type": "function", "function": {"name": "home_light_turn_on", "parameters": {}}}
        ],
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{proxy.url}/v1/chat/completions", json=body, headers={ROLE_HEADER: SYSTEM2}
        )
    assert r.json() == COMPLETION
    [call] = proxy.calls
    assert call.role == "system2" and call.tools_offered == ["home_light_turn_on"]
    assert call.tool_calls[0].arguments == {"target": "Bedroom Lamp"}
    assert call.prompt_tokens == 100 and call.status == 200
    assert call.request_body == body
    assert json.loads(proxy.seen[0].content) == body  # type: ignore[attr-defined]
    assert proxy.seen[0].headers[ROLE_HEADER] == SYSTEM2  # type: ignore[attr-defined]


async def test_passthrough_is_not_recorded(proxy: LlmProxy) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{proxy.url}/v1/models")
    assert r.json()["data"][0]["id"] == "m" and proxy.calls == []


async def test_streaming_is_refused(proxy: LlmProxy) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{proxy.url}/v1/chat/completions", json={"model": "m", "messages": [], "stream": True}
        )
    assert r.status_code == 400 and proxy.seen == []  # type: ignore[attr-defined]


async def test_upstream_failure_is_recorded_as_502() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    p = LlmProxy("http://vllm.test", transport=upstream(boom))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            body = {"model": "m", "messages": [], "metadata": {"private": "fake-secret"}}
            r = await client.post(f"{p.url}/v1/chat/completions", json=body)
        assert r.status_code == 502 and p.calls[0].status == 502
        assert p.calls[0].request_body == body
    finally:
        await p.stop()


async def test_concurrency_cap_covers_chat_and_passthrough_alike() -> None:
    active = 0
    peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", transport=httpx.MockTransport(slow))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            chat = [
                client.post(f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []})
                for _ in range(4)
            ]
            passthrough = [client.get(f"{p.url}/v1/models") for _ in range(2)]
            passthrough += [client.post(f"{p.url}/v1/completions", json={}) for _ in range(2)]
            await asyncio.gather(*chat, *passthrough)
        assert peak == MAX_UPSTREAM == 2
    finally:
        await p.stop()


async def test_answered_at_counts_the_wait_for_an_upstream_slot() -> None:
    """latency_ms is upstream's time only; answered_at - t is what the caller waited."""

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", max_concurrency=1, transport=httpx.MockTransport(slow))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            body = {"model": "m", "messages": []}
            await asyncio.gather(
                *(client.post(f"{p.url}/v1/chat/completions", json=body) for _ in range(2))
            )
        queued = max(p.calls, key=lambda c: c.answered_at or 0.0)
        assert queued.answered_at is not None
        assert queued.latency_ms < 190  # its own upstream call
        assert queued.answered_at - queued.t >= 0.19  # behind the other one's, too
    finally:
        await p.stop()


async def test_wait_idle_returns_once_the_call_in_flight_is_recorded() -> None:
    release = asyncio.Event()

    async def gated(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", transport=httpx.MockTransport(gated))
    await p.start()
    try:
        assert await p.wait_idle(0.0, 0.01)  # nothing in flight
        async with httpx.AsyncClient() as client:
            body = {"model": "m", "messages": []}
            sent = asyncio.create_task(client.post(f"{p.url}/v1/chat/completions", json=body))
            while not p.in_flight:
                await asyncio.sleep(0.005)
            assert not await p.wait_idle(0.0, 0.02) and p.calls == []
            waiting = asyncio.create_task(p.wait_idle(0.0, 5))
            await asyncio.sleep(0.01)
            release.set()
            assert await waiting
            assert len(p.calls) == 1 and p.in_flight == 0
            await sent
    finally:
        await p.stop()


async def test_a_failed_call_is_not_left_in_flight() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with serving(boom) as p, httpx.AsyncClient() as client:
        await client.post(f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []})
        assert p.in_flight == 0 and await p.wait_idle(0.0, 0.01)


SYSTEM1_BODY = {
    "model": "m",
    "messages": [{"role": "system", "content": "You are Alfred's Reflex Engine"}],
}


def is_system1(call: LlmCall) -> bool:
    return call.role == "system1"


async def test_wait_for_call_wakes_when_the_proxy_records_a_wanted_call() -> None:
    release = asyncio.Event()

    async def gated(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", transport=httpx.MockTransport(gated))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            waiting = asyncio.create_task(p.wait_for_call(time.monotonic(), 5, is_system1))
            sent = asyncio.create_task(
                client.post(
                    f"{p.url}/v1/chat/completions",
                    json=SYSTEM1_BODY,
                    headers={ROLE_HEADER: SYSTEM1},
                )
            )
            while not p.in_flight:
                await asyncio.sleep(0.005)
            assert not waiting.done()  # in flight is not recorded
            release.set()
            assert await waiting
            assert [c.role for c in p.calls] == ["system1"]
            await sent
    finally:
        await p.stop()


def call_at(t: float, role: Role) -> LlmCall:
    return LlmCall(t=t, role=role, latency_ms=1.0, status=200)


async def test_wait_for_call_counts_only_wanted_calls_since_and_times_out() -> None:
    p = LlmProxy("http://x")
    p.record(call_at(0.0, "system1"))  # before the wait's start
    since = time.monotonic()

    async def calls_land() -> None:
        await asyncio.sleep(0.05)
        p.record(call_at(time.monotonic(), "system2"))  # not wanted
        await asyncio.sleep(0.05)
        p.record(call_at(time.monotonic(), "system1"))

    landing = asyncio.create_task(calls_land())
    t0 = time.monotonic()
    assert await p.wait_for_call(since, 5, is_system1)
    await landing
    assert 0.1 <= time.monotonic() - t0 < 2
    assert await p.wait_for_call(since, 0)  # any call, when nothing narrows it
    t0 = time.monotonic()
    assert not await p.wait_for_call(time.monotonic(), 0.05, is_system1)
    assert time.monotonic() - t0 >= 0.05


async def test_wait_idle_waits_only_on_calls_sent_since() -> None:
    release = asyncio.Event()

    async def gated(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", transport=httpx.MockTransport(gated))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            body = {"model": "m", "messages": []}
            orphan = asyncio.create_task(client.post(f"{p.url}/v1/chat/completions", json=body))
            while not p.in_flight:
                await asyncio.sleep(0.005)
            since = time.monotonic()
            # Sent before *since*, so it can never enter a window starting there.
            assert await p.wait_idle(since, 0.01) and p.in_flight_since(since) == []
            assert not await p.wait_idle(0.0, 0.01)
            [stamp] = p.in_flight_since(0.0)
            assert stamp < since
            release.set()
            await orphan
            assert p.in_flight_since(0.0) == []
    finally:
        await p.stop()


SYSTEM2_BODY = {
    "model": "m",
    "messages": [{"role": "system", "content": "You are Alfred — personal butler"}],
}


async def test_waiting_on_one_role_is_not_held_by_another_roles_call() -> None:
    """A call in flight counts for a role once its body has been read and its header says whose
    it is; before then it could be anyone's, so it counts for every role."""
    release, body_sent = asyncio.Event(), asyncio.Event()

    async def gated(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json=COMPLETION)

    raw = json.dumps(SYSTEM2_BODY).encode()
    split = raw.index(b'"messages"')

    async def slow_body() -> AsyncIterator[bytes]:
        yield raw[:split]
        await body_sent.wait()
        yield raw[split:]

    p = LlmProxy("http://vllm.test", transport=httpx.MockTransport(gated))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            sent = asyncio.create_task(
                client.post(
                    f"{p.url}/v1/chat/completions",
                    content=slow_body(),
                    headers={"content-type": "application/json", ROLE_HEADER: SYSTEM2},
                )
            )
            async with asyncio.timeout(5):
                while not p.in_flight:  # the body is still on its way
                    await asyncio.sleep(0.005)
            [stamp] = p.in_flight_since(0.0, role="system1")  # whose, it cannot tell yet
            assert not await p.wait_idle(0.0, 0.02, role="system1")
            body_sent.set()
            # Read and found to be System 2's: no longer System 1's to wait for.
            assert await p.wait_idle(0.0, 5, role="system1")
            assert p.in_flight_since(0.0, role="system1") == []
            assert p.in_flight_since(0.0, role="system2") == [stamp]
            assert p.in_flight_since(0.0) == [stamp] and p.calls == []
            assert not await p.wait_idle(0.0, 0.02)
            release.set()
            await sent
            assert [c.role for c in p.calls] == ["system2"] and p.in_flight == 0
    finally:
        await p.stop()


async def test_a_call_is_in_flight_from_its_first_byte_not_its_last() -> None:
    body_sent = asyncio.Event()

    async def slow_body() -> AsyncIterator[bytes]:
        yield b'{"model": "m", '
        await body_sent.wait()
        yield b'"messages": []}'

    async with serving(lambda r: httpx.Response(200, json=COMPLETION)) as p:
        async with httpx.AsyncClient() as client:
            sent = asyncio.create_task(
                client.post(
                    f"{p.url}/v1/chat/completions",
                    content=slow_body(),
                    headers={"content-type": "application/json"},
                )
            )
            async with asyncio.timeout(5):  # fails, not hangs, when it never shows
                while not p.in_flight:  # the body is still on its way
                    await asyncio.sleep(0.005)
            seen_in_flight = time.monotonic()
            body_sent.set()
            assert (await sent).status_code == 200
        [call] = p.calls
        assert call.t <= seen_in_flight and p.in_flight == 0


def test_the_proxy_and_the_judge_share_one_vllm_budget_of_four() -> None:
    # vLLM is shared with production: whatever the eval sends it at once stays within 4.
    assert MAX_UPSTREAM + JUDGE_CONNECTIONS == 4
    judge = make_judge_model("m", "http://vllm.test/v1")
    assert judge.config.max_connections == JUDGE_CONNECTIONS


@pytest.mark.parametrize(
    ("headers", "role"),
    [
        ({ROLE_HEADER: SYSTEM1}, "system1"),
        ({ROLE_HEADER: SYSTEM2}, "system2"),
        ({ROLE_HEADER: LIBRARIAN}, "librarian"),
        ({ROLE_HEADER: APPROVAL}, "approval"),
        ({ROLE_HEADER: "System2"}, "unknown"),
        ({ROLE_HEADER: "reflex"}, "unknown"),
        ({}, "unknown"),
    ],
    ids=["system1", "system2", "librarian", "approval", "wrong-case", "not-a-role", "no-header"],
)
async def test_a_calls_role_is_its_role_header_and_never_its_prompt(
    headers: dict[str, str], role: Role
) -> None:
    # The prompt is System 2's. Only the header may say so: with no header, it is unknown.
    body = {
        "model": "m",
        "messages": [{"role": "system", "content": "You are Alfred — personal butler"}],
    }
    async with (
        serving(lambda r: httpx.Response(200, json=COMPLETION)) as p,
        httpx.AsyncClient() as client,
    ):
        await client.post(f"{p.url}/v1/chat/completions", json=body, headers=headers)
    [call] = p.calls
    assert call.role == role


def test_every_alfred_role_has_a_proxy_role() -> None:
    assert set(ROLES_BY_HEADER) == LLM_ROLES
    assert all(ROLES_BY_HEADER[r] == r for r in LLM_ROLES)


@asynccontextmanager
async def serving(handle: Callable[[httpx.Request], httpx.Response]) -> AsyncIterator[LlmProxy]:
    p = LlmProxy("http://vllm.test", transport=upstream(handle))
    await p.start()
    try:
        yield p
    finally:
        await p.stop()


async def test_passthrough_upstream_failure_is_a_json_502() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with serving(boom) as p, httpx.AsyncClient() as client:
        r = await client.get(f"{p.url}/v1/models")
    assert r.status_code == 502 and "refused" in r.json()["error"]["message"]
    assert p.calls == []


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b"\xff",
        b"[]",
        b'"a string"',
        b'{"model": "m", "messages": [], "temperature": NaN}',
        b'{"model": "m", "messages": [], "temperature": Infinity}',
        b'{"model": "m", "messages": [], "temperature": -Infinity}',
        b'{"model": "m", "messages": "abc"}',
        b'{"model": "m", "messages": {"role": "user", "content": "hi"}}',
        b'{"model": "m", "messages": null}',
        b'{"model": "m", "messages": [{"role": "user", "content": "hi"}, "hi"]}',
        # Nested past the parser's recursion limit.
        pytest.param(b"[" * 50_000 + b"]" * 50_000, id="nested"),
    ],
)
async def test_a_malformed_chat_body_is_a_400(proxy: LlmProxy, content: bytes) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{proxy.url}/v1/chat/completions",
            content=content,
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 400 and r.json()["error"]["message"]
    assert proxy.seen == [] and proxy.calls == []  # type: ignore[attr-defined]
    assert proxy.in_flight == 0  # a refused request leaves no stamp behind


async def test_forwards_the_request_bytes_alfred_sent(proxy: LlmProxy) -> None:
    raw = b'{"messages":[ ],  "model":"m", "temperature": 1.0e0}'
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{proxy.url}/v1/chat/completions",
            content=raw,
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 200 and proxy.seen[0].content == raw  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("tools", "offered"),
    [
        (
            [
                1,
                None,
                {"function": None},
                {"type": "function"},
                {"function": "x"},
                {"function": {"name": 7}},
                {"function": {"name": "home_light_turn_on"}},
            ],
            ["home_light_turn_on"],
        ),
        ("abc", []),
        ({"function": {"name": "x"}}, []),
    ],
)
async def test_tool_names_are_read_defensively(
    proxy: LlmProxy, tools: Any, offered: list[str]
) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{proxy.url}/v1/chat/completions", json={"model": "m", "messages": [], "tools": tools}
        )
    assert r.status_code == 200 and proxy.calls[0].tools_offered == offered


def tool_reply(arguments: Any) -> dict[str, Any]:
    call = {"id": "c1", "type": "function", "function": {"name": "x", "arguments": arguments}}
    return {"choices": [{"message": {"content": None, "tool_calls": [call]}}]}


@pytest.mark.parametrize(
    ("reply", "recorded"),
    [
        (tool_reply("null"), [{"name": "x", "arguments": {"_raw": "null"}}]),
        (tool_reply("[1]"), [{"name": "x", "arguments": {"_raw": "[1]"}}]),
        (tool_reply('"x"'), [{"name": "x", "arguments": {"_raw": '"x"'}}]),
        (tool_reply("{oops"), [{"name": "x", "arguments": {"_raw": "{oops"}}]),
        (tool_reply(5), [{"name": "x", "arguments": {"_raw": 5}}]),
        (
            tool_reply({"target": "Bedroom Lamp"}),
            [{"name": "x", "arguments": {"target": "Bedroom Lamp"}}],
        ),
        ({"choices": [{"message": {"tool_calls": ["x", None, 3]}}]}, []),
        # A name that is not a string reads as "", as tools_offered skips one.
        (
            {"choices": [{"message": {"tool_calls": [{"function": {"name": None}}]}}]},
            [{"name": "", "arguments": {}}],
        ),
        (
            {"choices": [{"message": {"tool_calls": [{"function": {"name": 7}}]}}]},
            [{"name": "", "arguments": {}}],
        ),
        ({"choices": "x", "usage": [1]}, []),
        ([], []),
        ([COMPLETION], []),
    ],
)
async def test_the_recorder_never_changes_a_reply(
    reply: Any, recorded: list[dict[str, Any]]
) -> None:
    raw = json.dumps(reply).encode()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    async with serving(handle) as p, httpx.AsyncClient() as client:
        r = await client.post(f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []})
    assert r.status_code == 200 and r.content == raw
    [call] = p.calls
    assert call.status == 200 and [tc.model_dump() for tc in call.tool_calls] == recorded


async def test_a_reply_nested_too_deeply_to_read_is_recorded_as_empty() -> None:
    raw = b"[" * 50_000 + b"]" * 50_000

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    async with serving(handle) as p, httpx.AsyncClient() as client:
        r = await client.post(f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []})
    assert r.content == raw
    [call] = p.calls
    assert call.response_text is None and call.tool_calls == []


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": "100", "completion_tokens": 7.5},
        {"prompt_tokens": True, "completion_tokens": None},
        {"prompt_tokens": -3, "completion_tokens": [1]},
        "many",
    ],
)
async def test_usage_that_is_not_a_count_is_dropped_but_the_call_is_kept(usage: Any) -> None:
    raw = json.dumps({**tool_reply({"target": "Lamp"}), "usage": usage}).encode()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    async with serving(handle) as p, httpx.AsyncClient() as client:
        await client.post(f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []})
    [call] = p.calls
    assert (call.prompt_tokens, call.completion_tokens) == (None, None)
    assert call.tool_calls[0].arguments == {"target": "Lamp"}


async def test_a_reply_the_recorder_cannot_record_still_goes_back(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps(COMPLETION).encode()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    def broken(content: bytes) -> NoReturn:
        raise RuntimeError("a bug in the recorder")

    monkeypatch.setattr(proxy_module, "_parse", broken)
    with caplog.at_level(logging.ERROR, logger="evals.harness.proxy"):
        async with serving(handle) as p, httpx.AsyncClient() as client:
            r = await client.post(
                f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []}
            )
    assert r.status_code == 200 and r.content == raw and p.calls == []
    assert "could not read a chat completion" in caplog.text


@pytest.mark.parametrize(
    ("upstream_type", "expected"),
    [("text/plain; charset=utf-8", "text/plain"), (None, "application/json")],
)
@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/models"])
async def test_mirrors_the_upstream_media_type(
    path: str, upstream_type: str | None, expected: str
) -> None:
    headers = {"content-type": upstream_type} if upstream_type else {}

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}", headers=headers)

    async with serving(handle) as p, httpx.AsyncClient() as client:
        r = await client.post(f"{p.url}{path}", json={"model": "m", "messages": []})
    assert r.status_code == 200 and r.headers["content-type"] == expected


# A made-up key and made-up hosts: no test here reaches a network.
FAKE_KEY = "fake-reflex-key-0123"
VLLM_HOST = "vllm.test"
HOSTED_HOST = "hosted.invalid"


@asynccontextmanager
async def routing(
    handle: Callable[[httpx.Request], httpx.Response],
    *,
    reflex_upstream: str | None = f"https://{HOSTED_HOST}/api",
    reflex_api_key: str | None = FAKE_KEY,
) -> AsyncIterator[LlmProxy]:
    p = LlmProxy(
        f"http://{VLLM_HOST}",
        reflex_upstream=reflex_upstream,
        reflex_api_key=reflex_api_key,
        transport=upstream(handle),
    )
    await p.start()
    try:
        yield p
    finally:
        await p.stop()


def _recording_upstream(seen: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=COMPLETION)

    return handle


async def test_a_system1_call_goes_to_the_reflex_upstream_with_the_key_and_no_other_does() -> None:
    seen: list[httpx.Request] = []
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    sent_by_alfred = {"Authorization": "Bearer what-alfred-sent"}
    async with routing(_recording_upstream(seen)) as p, httpx.AsyncClient() as client:
        for role in (SYSTEM1, SYSTEM2, LIBRARIAN, APPROVAL):
            headers = {ROLE_HEADER: role, **sent_by_alfred}
            r = await client.post(f"{p.url}/v1/chat/completions", json=body, headers=headers)
            assert r.status_code == 200 and r.json() == COMPLETION
        unmarked = await client.post(
            f"{p.url}/v1/chat/completions", json=body, headers=sent_by_alfred
        )
        assert unmarked.status_code == 200

    system1, *others = seen
    assert str(system1.url) == f"https://{HOSTED_HOST}/api/v1/chat/completions"
    # The key replaces the Authorization header Alfred sent, whatever its case.
    assert system1.headers.get_list("authorization") == [f"Bearer {FAKE_KEY}"]
    assert system1.headers[ROLE_HEADER] == SYSTEM1
    assert json.loads(system1.content) == body
    assert [str(o.url) for o in others] == [f"http://{VLLM_HOST}/v1/chat/completions"] * 4
    # Every other role gets vLLM, with the header it sent and not the key.
    assert [o.headers.get_list("authorization") for o in others] == [
        ["Bearer what-alfred-sent"]
    ] * 4
    assert [c.role for c in p.calls] == ["system1", "system2", "librarian", "approval", "unknown"]


async def test_the_key_is_nowhere_in_the_record_a_reply_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[httpx.Request] = []
    with caplog.at_level(logging.DEBUG):
        async with routing(_recording_upstream(seen)) as p, httpx.AsyncClient() as client:
            r = await client.post(
                f"{p.url}/v1/chat/completions",
                json={"model": "m", "messages": []},
                headers={ROLE_HEADER: SYSTEM1},
            )
    assert [req.headers["authorization"] for req in seen] == [f"Bearer {FAKE_KEY}"]
    [call] = p.calls
    assert call.role == "system1"
    assert FAKE_KEY not in call.model_dump_json()
    assert FAKE_KEY not in r.text and FAKE_KEY not in str(dict(r.headers))
    assert FAKE_KEY not in caplog.text


UNSAFE_KEY = "line\nbreak-0123"  # h11 refuses it, and quotes it with the newline escaped


@pytest.mark.parametrize("key", ["two words", "tab\tinside", "café", UNSAFE_KEY])
def test_a_key_that_cannot_be_a_header_value_is_refused_without_quoting_it(key: str) -> None:
    with pytest.raises(ValueError) as err:
        LlmProxy(
            f"http://{VLLM_HOST}", reflex_upstream=f"https://{HOSTED_HOST}", reflex_api_key=key
        )
    message = str(err.value)
    assert "key" in message
    assert key not in message and repr(key) not in message and repr(key)[1:-1] not in message


@pytest.mark.parametrize("reflex_upstream", [None, ""], ids=["none", "empty"])
def test_a_key_with_no_reflex_upstream_is_refused_so_it_can_never_go_to_vllm(
    reflex_upstream: str | None,
) -> None:
    with pytest.raises(ValueError, match="reflex_upstream") as err:
        LlmProxy(f"http://{VLLM_HOST}", reflex_upstream=reflex_upstream, reflex_api_key=FAKE_KEY)
    assert FAKE_KEY not in str(err.value)


@asynccontextmanager
async def _local_server() -> AsyncIterator[str]:
    """A real server on a loopback port that answers any chat completion: what a request
    reaches when nothing is faked between the proxy and the HTTP library."""

    async def answer(request: web.Request) -> web.Response:
        return web.json_response(COMPLETION)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", answer)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    try:
        yield f"http://127.0.0.1:{int(runner.addresses[0][1])}"
    finally:
        await runner.cleanup()


async def test_a_refusal_by_the_http_library_reaches_the_caller_as_its_class_alone() -> None:
    # The constructor keeps an unsafe key out, so the refusal is reached by putting one in
    # after it. Nothing is faked from there on: h11 itself refuses the header.
    async with _local_server() as reflex_url, httpx.AsyncClient() as client:
        with pytest.raises(httpx.LocalProtocolError) as refusal:
            await client.post(
                f"{reflex_url}/v1/chat/completions",
                content=b"{}",
                headers={"Authorization": f"Bearer {UNSAFE_KEY}"},
            )
        # What the library says is what must not reach anyone: it quotes the key, with the
        # newline escaped, so no substring match on the raw key would find it.
        assert "break-0123" in str(refusal.value) and UNSAFE_KEY not in str(refusal.value)

        p = LlmProxy(f"http://{VLLM_HOST}", reflex_upstream=reflex_url, reflex_api_key=FAKE_KEY)
        await p.start()
        try:
            p._reflex_key = UNSAFE_KEY
            r = await client.post(
                f"{p.url}/v1/chat/completions",
                json={"model": "m", "messages": []},
                headers={ROLE_HEADER: SYSTEM1},
            )
        finally:
            await p.stop()

    assert r.status_code == 502
    assert r.json()["error"]["message"] == "upstream failed: LocalProtocolError"
    assert "break" not in r.text
    [call] = p.calls
    assert call.status == 502 and "break" not in call.model_dump_json()


async def test_a_transport_error_on_the_reflex_path_is_reported_by_its_class_alone() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    async with routing(refuse) as p, httpx.AsyncClient() as client:
        r = await client.post(
            f"{p.url}/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers={ROLE_HEADER: SYSTEM1},
        )
    assert r.status_code == 502
    assert r.json()["error"]["message"] == "upstream failed: ConnectError"
    assert HOSTED_HOST not in r.text and FAKE_KEY not in r.text


@pytest.mark.parametrize(
    ("role", "reflex_upstream"),
    [(SYSTEM2, f"https://{HOSTED_HOST}/api"), (SYSTEM1, None)],
    ids=["other-role", "no-reflex-upstream"],
)
async def test_a_failure_that_never_touched_the_reflex_upstream_is_worded_as_before(
    role: str, reflex_upstream: str | None
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with (
        routing(refuse, reflex_upstream=reflex_upstream, reflex_api_key=None) as p,
        httpx.AsyncClient() as client,
    ):
        r = await client.post(
            f"{p.url}/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers={ROLE_HEADER: role},
        )
    assert r.status_code == 502
    assert r.json()["error"]["message"] == "upstream failed: connection refused"


async def test_passthrough_always_goes_to_vllm_without_the_key() -> None:
    seen: list[httpx.Request] = []
    async with routing(_recording_upstream(seen)) as p, httpx.AsyncClient() as client:
        r = await client.get(
            f"{p.url}/v1/models", headers={ROLE_HEADER: SYSTEM1, "Authorization": "Bearer theirs"}
        )
    assert r.status_code == 200 and p.calls == []
    [req] = seen
    assert str(req.url) == f"http://{VLLM_HOST}/v1/models"
    assert req.headers.get_list("authorization") == ["Bearer theirs"]


@pytest.mark.parametrize("key", [None, ""], ids=["unset", "empty"])
async def test_without_a_key_the_reflex_upstream_gets_the_header_alfred_sent(
    key: str | None,
) -> None:
    seen: list[httpx.Request] = []
    async with (
        routing(_recording_upstream(seen), reflex_api_key=key) as p,
        httpx.AsyncClient() as client,
    ):
        await client.post(
            f"{p.url}/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers={ROLE_HEADER: SYSTEM1, "Authorization": "Bearer what-alfred-sent"},
        )
        await client.post(
            f"{p.url}/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers={ROLE_HEADER: SYSTEM1},
        )
    with_header, without = seen
    assert str(with_header.url) == f"https://{HOSTED_HOST}/api/v1/chat/completions"
    assert with_header.headers.get_list("authorization") == ["Bearer what-alfred-sent"]
    assert "authorization" not in without.headers


async def test_every_role_goes_to_vllm_when_no_reflex_upstream_is_given() -> None:
    seen: list[httpx.Request] = []
    async with (
        routing(_recording_upstream(seen), reflex_upstream=None, reflex_api_key=None) as p,
        httpx.AsyncClient() as client,
    ):
        for role in (SYSTEM1, SYSTEM2):
            await client.post(
                f"{p.url}/v1/chat/completions",
                json={"model": "m", "messages": []},
                headers={ROLE_HEADER: role},
            )
    assert {str(r.url) for r in seen} == {f"http://{VLLM_HOST}/v1/chat/completions"}


@pytest.mark.parametrize("name", ["Authorization", "authorization", "AUTHORIZATION"])
async def test_the_key_replaces_alfreds_authorization_header_whatever_its_case(name: str) -> None:
    seen: list[httpx.Request] = []
    async with routing(_recording_upstream(seen)) as p, httpx.AsyncClient() as client:
        await client.post(
            f"{p.url}/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers={ROLE_HEADER: SYSTEM1, name: "Bearer what-alfred-sent"},
        )
    [req] = seen
    assert req.headers.get_list("authorization") == [f"Bearer {FAKE_KEY}"]
