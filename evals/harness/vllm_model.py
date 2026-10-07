"""An Inspect model provider for an OpenAI-compatible vLLM server, over plain httpx.

Inspect 0.3.277's own ``openai-api`` provider needs ``openai>=3.4``, which litellm (a base
dependency) forbids, so it cannot load in this environment. This provider needs only httpx:
``POST <base_url>/chat/completions``, text in and text out. Tools and streaming are not
supported. Inspect still owns retries (through ``should_retry``), ``max_connections`` and
``timeout``.

Use it as ``get_model("alfred-vllm/<served model>", base_url="http://host:8000/v1", ...)``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
from inspect_ai.model import (
    GenerateConfig,
    ModelAPI,
    ModelCall,
    ModelOutput,
    ModelUsage,
    RetryDecision,
    modelapi,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from inspect_ai.model import ChatMessage, StopReason
    from inspect_ai.tool import ToolChoice, ToolInfo

PROVIDER = "alfred-vllm"
DEFAULT_TIMEOUT_S = 120.0  # per request, when the config sets no ``timeout``
_EXCERPT = 300
_STOP_REASONS: dict[str, StopReason] = {"stop": "stop", "length": "max_tokens"}


class VllmStatusError(RuntimeError):
    """The server answered with an HTTP error. Retried for 5xx and 429, never other 4xx."""

    def __init__(self, url: str, status_code: int, body: str) -> None:
        super().__init__(f"{url} answered HTTP {status_code}: {body[:_EXCERPT]}")
        self.status_code = status_code


class VllmResponseError(RuntimeError):
    """The server answered 200 with something other than a chat completion. Never retried."""


@modelapi(name=PROVIDER)
class VllmChatAPI(ModelAPI):
    def __init__(
        self,
        model_name: str,
        base_url: str | None = None,
        api_key: str | None = None,
        config: GenerateConfig = GenerateConfig(),  # noqa: B008 — Inspect's own signature
        transport: httpx.AsyncBaseTransport | None = None,
        **model_args: Any,
    ) -> None:
        super().__init__(model_name, base_url, api_key, [], config)
        if not base_url:
            raise ValueError(f"{PROVIDER} needs base_url, such as http://localhost:8000/v1")
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._transport = transport  # tests inject an httpx.MockTransport

    def connection_key(self) -> str:
        # One pool per server and model, so max_connections caps what we send it.
        return f"{self.base_url}|{self.model_name}"

    def should_retry(self, ex: Exception) -> bool | RetryDecision:
        if isinstance(ex, httpx.TransportError):  # unreachable, reset, timed out
            return RetryDecision.transient()
        if isinstance(ex, VllmStatusError) and ex.status_code == 429:  # the back-off signal
            return RetryDecision.rate_limit()
        if isinstance(ex, VllmStatusError) and ex.status_code >= 500:
            return RetryDecision.transient()
        return RetryDecision.no()

    async def generate(
        self,
        input: list[ChatMessage],  # noqa: A002 — Inspect passes it by this keyword
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> tuple[ModelOutput, ModelCall]:
        if tools:
            raise NotImplementedError(f"{PROVIDER} does not support tools")
        body: dict[str, Any] = {"model": self.model_name, "messages": to_openai_messages(input)}
        if config.temperature is not None:
            body["temperature"] = config.temperature
        if config.max_tokens is not None:
            body["max_tokens"] = config.max_tokens
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        timeout = float(config.timeout) if config.timeout is not None else DEFAULT_TIMEOUT_S
        async with httpx.AsyncClient(transport=self._transport, timeout=timeout) as client:
            response = await client.post(self._url, json=body, headers=headers)
        if response.is_error:
            raise VllmStatusError(self._url, response.status_code, response.text)
        data, output = parse_completion(response.text, self.model_name, self._url)
        return output, ModelCall.create(request=body, response=data)


def to_openai_messages(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    """Text-only OpenAI chat messages. A tool message is refused, not dropped."""
    converted: list[dict[str, str]] = []
    for message in messages:
        if message.role == "tool":
            raise NotImplementedError(f"{PROVIDER} does not support tool messages")
        converted.append({"role": message.role, "content": message.text})
    return converted


def parse_completion(text: str, model: str, url: str) -> tuple[dict[str, Any], ModelOutput]:
    def malformed(why: str) -> VllmResponseError:
        return VllmResponseError(
            f"{url} did not return a chat completion ({why}): {text[:_EXCERPT]}"
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise malformed("not JSON") from None
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise malformed("no choices[0].message.content") from None
    if not isinstance(content, str):
        raise malformed("the content is not text")
    stop_reason = _STOP_REASONS.get(str(choice.get("finish_reason")), "unknown")
    output = ModelOutput.from_content(model=model, content=content, stop_reason=stop_reason)
    usage = data.get("usage")
    if isinstance(usage, dict):
        output.usage = ModelUsage(
            input_tokens=usage.get("prompt_tokens") or 0,
            output_tokens=usage.get("completion_tokens") or 0,
            total_tokens=usage.get("total_tokens") or 0,
        )
    return data, output
