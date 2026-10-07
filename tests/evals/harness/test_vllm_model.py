from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    GenerateConfig,
    get_model,
)

from evals.harness.vllm_model import PROVIDER, VllmResponseError, VllmStatusError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from inspect_ai.model import Model

BASE_URL = "http://vllm.test/v1"
COMPLETION = {
    "model": "judge-m",
    "choices": [
        {"message": {"role": "assistant", "content": "VERDICT: yes"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 42, "completion_tokens": 3, "total_tokens": 45},
}


def vllm(
    handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]],
    **config: Any,
) -> Model:
    return get_model(
        f"{PROVIDER}/judge-m",
        base_url=BASE_URL,
        api_key="alfred-eval-not-a-key",
        config=GenerateConfig(**config),
        memoize=False,
        transport=httpx.MockTransport(handler),
    )


def answering(body: dict[str, Any], status: int = 200) -> tuple[Model, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return vllm(handler, temperature=0.0, max_tokens=600, max_retries=0), seen


async def test_request_carries_the_conversation_and_settings() -> None:
    model, seen = answering(COMPLETION)
    await model.generate(
        [
            ChatMessageSystem(content="You grade replies."),
            ChatMessageUser(content="Is this formal?"),
            ChatMessageAssistant(content="Let me think."),
            ChatMessageUser(content="Answer now."),
        ]
    )
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["Authorization"] == "Bearer alfred-eval-not-a-key"
    assert json.loads(request.content) == {
        "model": "judge-m",
        "messages": [
            {"role": "system", "content": "You grade replies."},
            {"role": "user", "content": "Is this formal?"},
            {"role": "assistant", "content": "Let me think."},
            {"role": "user", "content": "Answer now."},
        ],
        "temperature": 0.0,
        "max_tokens": 600,
    }


async def test_content_and_usage_come_back() -> None:
    model, _ = answering(COMPLETION)
    output = await model.generate("Is this formal?")
    assert output.completion == "VERDICT: yes"
    assert output.stop_reason == "stop"
    assert output.usage is not None
    assert (output.usage.input_tokens, output.usage.output_tokens) == (42, 3)
    assert output.usage.total_tokens == 45


async def test_a_reply_without_usage_still_comes_back() -> None:
    model, _ = answering({"choices": [{"message": {"content": "VERDICT: no"}}]})
    assert (await model.generate("Is this formal?")).completion == "VERDICT: no"


async def one_call(model: Model) -> None:
    """One attempt straight through the provider, so its own exception reaches the test.

    ``Model.generate`` wraps a retryable error in ``tenacity.RetryError`` once retries
    run out; ``Judge.ask`` unwraps it (see test_judge.py).
    """
    await model.api.generate([ChatMessageUser(content="Is this formal?")], [], "auto", model.config)


async def test_a_server_error_is_retried_and_a_client_error_is_not() -> None:
    for status, retried in ((503, True), (500, True), (429, True), (400, False), (404, False)):
        model, _ = answering({"error": {"message": "nope"}}, status=status)
        with pytest.raises(VllmStatusError) as caught:
            await one_call(model)
        assert caught.value.status_code == status
        assert bool(model.api.should_retry(caught.value)) is retried, status
    assert "HTTP 404" in str(caught.value) and "nope" in str(caught.value)


async def test_a_client_error_fails_at_once_through_the_model() -> None:
    model, seen = answering({"error": {"message": "model not found"}}, status=400)
    with pytest.raises(VllmStatusError, match="model not found"):
        await model.generate("Is this formal?")
    assert len(seen) == 1


async def test_a_server_that_cannot_be_reached_is_retried() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    model = vllm(refuse, max_retries=0)
    with pytest.raises(httpx.ConnectError) as caught:
        await one_call(model)
    assert model.api.should_retry(caught.value)
    assert model.api.should_retry(httpx.ReadTimeout("slow"))


@pytest.mark.parametrize(
    "body",
    [
        "<html>gateway</html>",
        json.dumps({"object": "error"}),
        json.dumps({"choices": []}),
        json.dumps({"choices": [{"message": {"content": None}}]}),
    ],
)
async def test_a_malformed_body_is_a_clear_error_and_not_retried(body: str) -> None:
    model = vllm(lambda _: httpx.Response(200, text=body), max_retries=0)
    with pytest.raises(VllmResponseError, match="did not return a chat completion") as caught:
        await model.generate("Is this formal?")  # not retryable, so it is not wrapped
    assert not model.api.should_retry(caught.value)


async def test_tools_and_tool_messages_are_refused() -> None:
    model, seen = answering(COMPLETION)
    with pytest.raises(NotImplementedError, match="tool"):
        await model.generate([ChatMessageTool(content="42", tool_call_id="call-1")])
    assert seen == []


async def test_max_connections_bounds_the_requests_in_flight() -> None:
    in_flight, peak = 0, 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, json=COMPLETION)

    model = vllm(slow, max_connections=2, max_retries=0)
    outputs = await asyncio.gather(*(model.generate(f"question {i}") for i in range(6)))
    assert [o.completion for o in outputs] == ["VERDICT: yes"] * 6
    assert peak == 2


async def test_the_config_timeout_bounds_each_request() -> None:
    timeouts: list[dict[str, float | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json=COMPLETION)

    await vllm(handler, timeout=120, max_retries=0).generate("Is this formal?")
    assert timeouts[0]["read"] == 120 and timeouts[0]["connect"] == 120
