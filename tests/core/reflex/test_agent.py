"""The Reflex agent: a Pydantic AI agent whose request is pinned on the wire."""

from __future__ import annotations

import dataclasses
import json
import re
import threading
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.vllm import VLLMProvider

import shared.llm_http as llm_http
from core.reflex import agent as agent_module
from core.reflex.agent import build_reflex_agent, decide, reply_format
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.telemetry import get_telemetry_buffer
from shared.agents import resolve_model
from shared.prompts.names import REFLEX_RULES
from shared.prompts.template import repo_text
from tests.core.reflex.fakes import reflex_agent

if TYPE_CHECKING:
    from collections.abc import Iterator

PROMPT = "Decide about one change.\n\nTools:\n- home.light_turn_on(target)\n\n## What changed\nx"

TOOLS = [
    ToolInfo(
        name="home.light_turn_on",
        description="light.turn_on",
        feature_name="home",
        feature_description="",
        target_service="home-service",
        audience="reflex",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    )
]

ASK = (
    '{"decision": "ask", "tool_name": "home.light_turn_on", '
    '"parameters": {"target": "Lamp"}, "reason": "dark"}'
)

NONE_REPLY: dict[str, Any] = {
    "type": "object",
    "properties": {"decision": {"const": "none"}},
    "required": ["decision"],
    "additionalProperties": False,
}

# The reply format a call with TOOLS sends: "none", or an act or ask that names one of TOOLS.
REPLY_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "reflex_reply",
        "schema": {
            "anyOf": [
                NONE_REPLY,
                {
                    "type": "object",
                    "properties": {
                        "decision": {"enum": ["act", "ask"]},
                        "reason": {"type": "string"},
                        "tool_name": {"enum": ["home.light_turn_on"]},
                        "parameters": {"type": "object"},
                    },
                    "required": ["decision", "reason", "tool_name", "parameters"],
                    "additionalProperties": False,
                },
            ]
        },
    },
}


def _reply_schema(settings: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = settings["extra_body"]["response_format"]["json_schema"]["schema"]
    return schema


# --- the reply text -------------------------------------------------------------------


async def test_the_reply_text_is_parsed() -> None:
    agent, _ = reflex_agent(ASK)

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "ask"
    assert proposal.action is not None
    assert proposal.action.source == "reflex-engine"
    assert proposal.action.tool_name == "home.light_turn_on"


async def test_the_reply_is_parsed_on_the_event_loop_not_a_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sync output function would run in a worker thread: a hop on every decision."""
    parsed_on: list[int] = []
    real = agent_module.parse_decision

    def spy(text: str, tools: list[ToolInfo]) -> Any:
        parsed_on.append(threading.get_ident())
        return real(text, tools)

    monkeypatch.setattr(agent_module, "parse_decision", spy)
    agent, _ = reflex_agent(ASK)

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "ask"
    assert parsed_on == [threading.get_ident()]


async def test_the_parse_is_checked_against_the_tools_of_this_call() -> None:
    agent, _ = reflex_agent(ASK)

    proposal = await decide(agent, PROMPT, [])

    assert proposal.decision == "invalid"
    assert proposal.problem == "tool 'home.light_turn_on' is not one of Reflex's tools"


async def test_an_unparseable_reply_is_invalid_and_not_retried() -> None:
    agent, script = reflex_agent("hello")

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.problem == "not JSON"
    assert proposal.raw == "hello"
    assert script.call_count == 1


async def test_an_empty_reply_is_invalid_and_not_retried() -> None:
    agent, script = reflex_agent("")

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.raw == ""
    assert script.call_count == 1


async def test_a_transport_failure_propagates() -> None:
    agent, script = reflex_agent(ConnectionError("down"))

    with pytest.raises(ConnectionError):
        await decide(agent, PROMPT, TOOLS)

    assert script.call_count == 1


# --- what the model is sent -----------------------------------------------------------


async def test_the_whole_prompt_is_the_single_user_message_with_no_instructions() -> None:
    agent, script = reflex_agent('{"decision": "none"}')

    await decide(agent, PROMPT, TOOLS)

    assert script.prompt() == PROMPT
    assert script.instructions() == ""
    assert script.offered() == []  # no tools, so the request carries none


async def test_the_settings_are_pinned() -> None:
    agent, script = reflex_agent('{"decision": "none"}')

    await decide(agent, PROMPT, TOOLS)

    settings = script.settings()
    assert settings["temperature"] == 0.0
    assert settings["max_tokens"] == 150
    assert settings["timeout"] == 30.0  # the old client's, not Pydantic AI's ten minutes
    assert settings["extra_headers"] == {"X-Alfred-Role": "system1"}
    assert settings["extra_body"] == {"response_format": REPLY_FORMAT}


async def test_the_reply_must_name_one_of_the_tools_of_this_call() -> None:
    """The server holds the reply to the format: the model cannot write a tool it was not shown."""
    agent, script = reflex_agent('{"decision": "none"}', '{"decision": "none"}')
    cover = dataclasses.replace(TOOLS[0], name="home.cover_open")

    await decide(agent, PROMPT, [*TOOLS, cover])
    await decide(agent, PROMPT, TOOLS)

    [_, choose] = _reply_schema(script.settings(0))["anyOf"]
    assert choose["properties"]["tool_name"] == {"enum": ["home.cover_open", "home.light_turn_on"]}
    assert _reply_schema(script.settings(1)) == REPLY_FORMAT["json_schema"]["schema"]


async def test_with_no_tools_the_only_reply_is_none() -> None:
    agent, script = reflex_agent('{"decision": "none"}')

    await decide(agent, PROMPT, [])

    assert _reply_schema(script.settings()) == NONE_REPLY


def test_the_reply_format_is_the_reply_the_rules_prompt_asks_for() -> None:
    """The server enforces the schema, so a reply line it disagrees with would be ignored."""
    line = repo_text(REFLEX_RULES).rpartition("Respond with JSON only.")[2]
    [none, act_or_ask] = reply_format(TOOLS)["json_schema"]["schema"]["anyOf"]

    assert re.findall(r'"(\w+)":', line) == [*none["properties"], *act_or_ask["properties"]]
    decisions = re.findall(r'"decision": ("\w+"(?: \| "\w+")*)', line)
    assert [re.findall(r"\w+", d) for d in decisions] == [
        [none["properties"]["decision"]["const"]],
        act_or_ask["properties"]["decision"]["enum"],
    ]


# --- the wire -------------------------------------------------------------------------


class Upstream:
    """An OpenAI-compatible server that answers every request and keeps what it was sent."""

    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx2.Response(self.status, json={"error": {"message": "unavailable"}})
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"decision": "none"}'},
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            },
        )

    @property
    def body(self) -> dict[str, Any]:
        assert len(self.requests) == 1
        body: dict[str, Any] = json.loads(self.requests[0].content)
        return body

    @property
    def headers(self) -> httpx2.Headers:
        return self.requests[0].headers


@pytest.fixture
def upstream() -> Iterator[Upstream]:
    with override_allow_model_requests(True):
        yield Upstream()


def _vllm(upstream: Upstream, *, api_key: str | None = None) -> OpenAIChatModel:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(upstream.handler))
    provider = VLLMProvider(base_url="http://vllm.test/v1", api_key=api_key, http_client=client)
    return OpenAIChatModel("test-model", provider=provider)


async def test_the_request_is_pinned_on_the_wire(upstream: Upstream) -> None:
    await decide(build_reflex_agent(_vllm(upstream)), PROMPT, TOOLS)

    assert str(upstream.requests[0].url) == "http://vllm.test/v1/chat/completions"
    assert upstream.headers["x-alfred-role"] == "system1"
    # The whole body, not a subset: a field this request should not send would show here.
    # `max_completion_tokens` is how Pydantic AI spells the `max_tokens` setting for an
    # OpenAI-compatible server; vLLM reads either.
    assert upstream.body == {
        "model": "test-model",
        "stream": False,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_completion_tokens": 150,
        "temperature": 0.0,
        "response_format": REPLY_FORMAT,
    }


async def test_a_keyed_provider_sends_its_key(upstream: Upstream) -> None:
    await decide(build_reflex_agent(_vllm(upstream, api_key="k")), PROMPT, TOOLS)

    assert upstream.headers["authorization"] == "Bearer k"


async def test_the_configured_model_sends_the_same_request(
    monkeypatch: pytest.MonkeyPatch, upstream: Upstream
) -> None:
    """``REFLEX_MODEL=vllm:<name>`` as the process resolves it, retrying transport included."""
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.test/v1")
    monkeypatch.setattr(llm_http, "_base_transport", lambda: httpx2.MockTransport(upstream.handler))
    model = resolve_model("REFLEX_MODEL", "vllm:test-model")
    assert model is not None

    await decide(build_reflex_agent(model), PROMPT, TOOLS)

    assert upstream.headers["x-alfred-role"] == "system1"
    assert upstream.body["messages"] == [{"role": "user", "content": PROMPT}]
    assert upstream.body["response_format"] == REPLY_FORMAT
    assert upstream.body["temperature"] == 0.0
    assert "tools" not in upstream.body


async def test_a_refused_request_raises_so_the_entry_stays_pending() -> None:
    server = Upstream(status=400)  # not retried, so the test does not wait on a backoff

    with override_allow_model_requests(True), pytest.raises(ModelHTTPError) as caught:
        await decide(build_reflex_agent(_vllm(server)), PROMPT, TOOLS)

    assert caught.value.status_code == 400
    assert len(server.requests) == 1


# --- telemetry ------------------------------------------------------------------------


async def test_tokens_are_recorded_under_the_research_series_label(upstream: Upstream) -> None:
    await decide(build_reflex_agent(_vllm(upstream)), PROMPT, TOOLS)

    [entry] = [e for e in get_telemetry_buffer() if e["metric_type"] == "tokens"]
    assert entry["model"] == "openai-compat"
    assert entry["prompt_tokens"] == 11
    assert entry["completion_tokens"] == 5
    assert entry["total_tokens"] == 16
