from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, NoReturn

import httpx
import pytest

from evals.harness import proxy as proxy_module
from evals.harness.judge import JUDGE_CONNECTIONS, make_judge_model
from evals.harness.proxy import MAX_UPSTREAM, LlmProxy, classify_role

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


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
        r = await client.post(f"{proxy.url}/v1/chat/completions", json=body)
    assert r.json() == COMPLETION
    [call] = proxy.calls
    assert call.role == "system2" and call.tools_offered == ["home_light_turn_on"]
    assert call.tool_calls[0].arguments == {"target": "Bedroom Lamp"}
    assert call.prompt_tokens == 100 and call.status == 200
    assert json.loads(proxy.seen[0].content) == body  # type: ignore[attr-defined]


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
            r = await client.post(
                f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []}
            )
        assert r.status_code == 502 and p.calls[0].status == 502
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


async def test_wait_idle_returns_once_the_call_in_flight_is_recorded() -> None:
    release = asyncio.Event()

    async def gated(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", transport=httpx.MockTransport(gated))
    await p.start()
    try:
        assert await p.wait_idle(0.01)  # nothing in flight
        async with httpx.AsyncClient() as client:
            body = {"model": "m", "messages": []}
            sent = asyncio.create_task(client.post(f"{p.url}/v1/chat/completions", json=body))
            while not p.in_flight:
                await asyncio.sleep(0.005)
            assert not await p.wait_idle(0.02) and p.calls == []
            waiting = asyncio.create_task(p.wait_idle(5))
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
        assert p.in_flight == 0 and await p.wait_idle(0.01)


def test_the_proxy_and_the_judge_share_one_vllm_budget_of_four() -> None:
    # vLLM is shared with production: whatever the eval sends it at once stays within 4.
    assert MAX_UPSTREAM + JUDGE_CONNECTIONS == 4
    judge = make_judge_model("m", "http://vllm.test/v1")
    assert judge.config.max_connections == JUDGE_CONNECTIONS


@pytest.mark.parametrize(
    ("text", "role"),
    [
        ("You are Alfred's Reflex Engine — a fast-acting steward for a smart home.", "system1"),
        ("You are Alfred — personal butler and assistant to sir.\n...", "system2"),
        ("You are a pattern analyst for ...", "librarian"),
        ("hello", "unknown"),
    ],
)
def test_classify_role(text: str, role: str) -> None:
    assert classify_role([{"role": "system", "content": text}]) == role
    assert classify_role([{"role": "user", "content": [{"type": "text", "text": text}]}]) == role


@asynccontextmanager
async def serving(handle: Callable[[httpx.Request], httpx.Response]) -> AsyncIterator[LlmProxy]:
    p = LlmProxy("http://vllm.test", transport=upstream(handle))
    await p.start()
    try:
        yield p
    finally:
        await p.stop()


def test_classify_role_skips_text_parts_it_cannot_read() -> None:
    content = [
        {"type": "text", "text": 5},
        "x",
        {"type": "text", "text": "You are a memory analyst"},
    ]
    assert classify_role([{"role": "user", "content": content}]) == "librarian"


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
