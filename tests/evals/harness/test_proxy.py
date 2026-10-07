from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from evals.harness.proxy import ROLE_FINGERPRINTS, LlmProxy, classify_role

REPO = Path(__file__).resolve().parents[3]

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


async def test_concurrency_cap() -> None:
    active = 0
    peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", max_concurrency=2, transport=httpx.MockTransport(slow))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            await asyncio.gather(
                *[
                    client.post(f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []})
                    for _ in range(6)
                ]
            )
        assert peak == 2
    finally:
        await p.stop()


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


def test_every_fingerprint_still_exists_in_the_prompts() -> None:
    sources = "".join(
        (REPO / p).read_text()
        for p in (
            "core/conscious/prompts/personality.md",
            "core/reflex/engine.py",
            "core/librarian/consolidator.py",
        )
    )
    for _, fingerprint in ROLE_FINGERPRINTS:
        assert fingerprint in sources, f"prompt changed; update ROLE_FINGERPRINTS: {fingerprint!r}"


async def test_passthrough_upstream_failure_is_a_json_502() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    p = LlmProxy("http://vllm.test", transport=upstream(boom))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{p.url}/v1/models")
        assert r.status_code == 502 and "refused" in r.json()["error"]["message"]
        assert p.calls == []
    finally:
        await p.stop()


@pytest.mark.parametrize("content", [b"{not json", b"\xff", b"[]", b'"a string"'])
async def test_a_chat_body_that_is_not_a_json_object_is_a_400(
    proxy: LlmProxy, content: bytes
) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{proxy.url}/v1/chat/completions",
            content=content,
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 400 and r.json()["error"]["message"]
    assert proxy.seen == [] and proxy.calls == []  # type: ignore[attr-defined]


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

    p = LlmProxy("http://vllm.test", transport=upstream(handle))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{p.url}{path}", json={"model": "m", "messages": []})
        assert r.status_code == 200 and r.headers["content-type"] == expected
    finally:
        await p.stop()
