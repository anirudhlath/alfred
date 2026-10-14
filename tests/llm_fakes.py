"""A scripted model for agent tests: the replies the model gives, in order.

A reply is one of:

- ``str``: plain text;
- a ``list`` or ``dict``: structured output. For a model whose profile supports JSON-schema
  output it is sent as JSON text (a list wrapped as ``{"response": [...]}``, as
  ``NativeOutput`` expects); otherwise it is sent as a call to the agent's output tool;
- a ``ModelResponse``: sent as it is, e.g. ``tool_call(...)``;
- an exception: raised from the model, as a failed request would be.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles import ModelProfile

Reply = str | list[Any] | dict[str, Any] | ModelResponse | BaseException


class ScriptExhausted(AssertionError):  # noqa: N818 — the name later tasks import
    """The agent asked the model for more replies than the test scripted."""


def tool_call(name: str, args: dict[str, Any] | str | None = None) -> ModelResponse:
    """One tool call. *args* as a string is sent raw, so a test can send malformed JSON."""
    return ModelResponse(
        parts=[ToolCallPart(tool_name=name, args=args if args is not None else {})]
    )


def tool_calls(*calls: tuple[str, dict[str, Any]]) -> ModelResponse:
    """Several tool calls in one response."""
    return ModelResponse(parts=[ToolCallPart(tool_name=n, args=a) for n, a in calls])


class Script:
    """A ``FunctionModel`` that answers with *replies* in order and records every request."""

    def __init__(self, *replies: Reply, json_schema_output: bool = True) -> None:
        self._replies = list(replies)
        self._native = json_schema_output
        self.requests: list[tuple[list[ModelMessage], AgentInfo]] = []
        self.model = FunctionModel(
            self._answer, profile=ModelProfile(supports_json_schema_output=json_schema_output)
        )

    def _answer(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append((list(messages), info))
        if not self._replies:
            raise ScriptExhausted(f"request {len(self.requests)} has no scripted reply")
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, ModelResponse):
            return reply
        if isinstance(reply, str):
            return ModelResponse(parts=[TextPart(reply)])
        payload: dict[str, Any] = {"response": reply} if isinstance(reply, list) else reply
        if self._native:
            return ModelResponse(parts=[TextPart(json.dumps(payload))])
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=payload)]
        )

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def instructions(self, i: int = 0) -> str:
        """The instructions the agent sent with request *i*."""
        return self.requests[i][1].instructions or ""

    def offered(self, i: int = 0) -> list[str]:
        """The function tools request *i* offered, by wire name."""
        return [t.name for t in self.requests[i][1].function_tools]

    def schema(self, name: str, i: int = 0) -> dict[str, Any]:
        """The parameters JSON Schema request *i* offered for tool *name*."""
        tool = next(t for t in self.requests[i][1].function_tools if t.name == name)
        return tool.parameters_json_schema

    def settings(self, i: int = 0) -> dict[str, Any]:
        """The model settings request *i* was made with."""
        return dict(self.requests[i][1].model_settings or {})

    def _last_request_parts(self, i: int) -> list[Any]:
        last = self.requests[i][0][-1]
        return list(last.parts) if isinstance(last, ModelRequest) else []

    def returns(self, i: int) -> list[str]:
        """The tool results request *i* carried, as the model reads them.

        A failed result (``ToolFailed``) is its bare message: the ``{"error": ...}`` wrapper
        Pydantic AI adds for providers without a native error channel is left off.
        """
        return [
            p.model_response_str(wrap_if_error=False)
            for p in self._last_request_parts(i)
            if isinstance(p, ToolReturnPart)
        ]

    def retries(self, i: int) -> list[str]:
        """The retry prompts request *i* carried (unknown tools, invalid arguments)."""
        return [
            p.model_response()
            for p in self._last_request_parts(i)
            if isinstance(p, RetryPromptPart)
        ]
