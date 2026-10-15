"""The Langfuse prompts client: auth, paths, the 404 rule and error mapping."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import httpx
import pytest

from shared.prompts.langfuse import LangfuseClient, LangfuseError, LangfusePrompt


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "system2-personality",
        "version": 4,
        "type": "text",
        "prompt": "You are Alfred.",
        "labels": ["production", "latest"],
        "config": {},
    }
    body.update(overrides)
    return body


def _client(handler: Any) -> LangfuseClient:
    return LangfuseClient(
        "http://langfuse.test/",
        public_key="pk-test",
        secret_key="sk-test",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_get_sends_basic_auth_and_the_label_and_parses_a_text_prompt() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_body())

    prompt = await _client(handler).get("system2-personality", label="production")

    assert prompt == LangfusePrompt(
        "system2-personality", 4, "text", "You are Alfred.", ("production", "latest")
    )
    (request,) = seen
    assert request.url.path == "/api/public/v2/prompts/system2-personality"
    assert request.url.params["label"] == "production"
    expected = base64.b64encode(b"pk-test:sk-test").decode()
    assert request.headers["authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
async def test_get_returns_none_for_an_unseeded_prompt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Prompt not found", "error": "NotFound"})

    assert await _client(handler).get("system2-personality", label="production") is None


@pytest.mark.asyncio
async def test_a_chat_prompt_has_no_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(type="chat", prompt=[{"role": "system"}]))

    prompt = await _client(handler).get("system2-personality", label="production")
    assert prompt is not None and (prompt.type, prompt.text) == ("chat", None)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"message": "Unauthorized"}),
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"name": "x"}),
    ],
    ids=["401", "500", "not-json", "wrong-shape"],
)
@pytest.mark.asyncio
async def test_failures_are_langfuse_errors(response: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    with pytest.raises(LangfuseError):
        await _client(handler).get("system2-personality", label="production")


@pytest.mark.asyncio
async def test_secrets_never_reach_the_error_text_or_its_cause() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {request.url} {dict(request.headers)}", request=request)

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    for handler in (refuse, unauthorized):
        with pytest.raises(LangfuseError) as caught:
            await _client(handler).get("system2-personality", label="production")
        assert "sk-test" not in str(caught.value)
        assert "sk-test" not in repr(caught.value.__cause__)
        secret_b64 = base64.b64encode(b"pk-test:sk-test").decode()
        assert secret_b64 not in str(caught.value)


@pytest.mark.asyncio
async def test_a_hanging_server_is_cut_off_by_a_total_deadline() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=_body())

    client = LangfuseClient(
        "http://langfuse.test",
        public_key="pk-test",
        secret_key="sk-test",
        timeout_s=0.05,
        transport=httpx.MockTransport(handler),
    )
    started = time.monotonic()
    with pytest.raises(LangfuseError):
        await client.get("system2-personality", label="production")
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_an_invalid_url_raised_in_transit_is_a_langfuse_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("bad")

    with pytest.raises(LangfuseError):
        await _client(handler).get("system2-personality", label="production")


@pytest.mark.parametrize("call", ["create", "set_labels"])
@pytest.mark.asyncio
async def test_only_a_get_treats_404_as_absent(call: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Prompt not found"})

    client = _client(handler)
    with pytest.raises(LangfuseError):
        if call == "create":
            await client.create("p", "text", labels=[], commit_message="m")
        else:
            await client.set_labels("p", 1, ["production"])


@pytest.mark.asyncio
async def test_create_posts_a_text_prompt_with_labels_and_a_commit_message() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/api/public/v2/prompts")
        seen.append(json.loads(request.content))
        return httpx.Response(201, json=_body(version=5, labels=["latest"]))

    created = await _client(handler).create(
        "system2-personality", "You are Alfred.", labels=[], commit_message="alfred prompts push"
    )

    assert created.version == 5
    assert seen == [
        {
            "type": "text",
            "name": "system2-personality",
            "prompt": "You are Alfred.",
            "labels": [],
            "config": {},
            "commitMessage": "alfred prompts push",
        }
    ]


@pytest.mark.asyncio
async def test_set_labels_patches_the_version() -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=_body(labels=["production", "latest"]))

    await _client(handler).set_labels("system2-personality", 4, ["production"])

    assert seen == [
        (
            "PATCH",
            "/api/public/v2/prompts/system2-personality/versions/4",
            {"newLabels": ["production"]},
        )
    ]
