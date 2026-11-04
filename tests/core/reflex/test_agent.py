"""The Reflex agent: a Pydantic AI agent whose request is pinned on the wire."""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import threading
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.vllm import VLLMProvider

import shared.llm_http as llm_http
from core.reflex import agent as agent_module
from core.reflex.agent import build_reflex_agent, decide, reply_format
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.telemetry import get_telemetry_buffer
from shared.agents import resolve_model
from shared.config import REFLEX_OUTPUTS
from shared.prompts.names import REFLEX_RULES
from shared.prompts.template import repo_text
from tests.core.reflex.fakes import reflex_agent
from tests.llm_fakes import tool_call

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pydantic_ai.models.function import AgentInfo

    from shared.config import ReflexOutput

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

    def __init__(self, *, status: int = 200, message: dict[str, Any] | None = None) -> None:
        self.status = status
        self.message = message or {"role": "assistant", "content": '{"decision": "none"}'}
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
                        "message": self.message,
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


# --- the other output shapes ----------------------------------------------------------

STRUCTURED = ["tool", "native", "json"]
COVER = dataclasses.replace(TOOLS[0], name="home.cover_open")

ASK_OBJECT: dict[str, Any] = json.loads(ASK)
NONE_OBJECT: dict[str, Any] = {"decision": "none"}

ACT_OR_ASK: dict[str, Any] = REPLY_FORMAT["json_schema"]["schema"]["anyOf"][1]

# The text shape's anyOf as one object schema, which is how a structured shape sends it
# (Pydantic AI wants an object at the top). `additionalProperties: true` is spelled out at the
# top and on `parameters`: the vLLM profile closes every object that does not say so, which
# would leave the top level no key to write and `parameters` none to hold.
REPLY_OBJECT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "anyOf": [
        NONE_REPLY,
        {
            **ACT_OR_ASK,
            "properties": {
                **ACT_OR_ASK["properties"],
                "parameters": {"type": "object", "additionalProperties": True},
            },
        },
    ],
}

# The same, after the vLLM profile's transformer: an object always has `properties`. Nothing
# else changes, so every field stays required where `text` requires it and no key opens up.
WIRE_REPLY_OBJECT: dict[str, Any] = {
    **REPLY_OBJECT,
    "properties": {},
    "anyOf": [
        NONE_REPLY,
        {
            **REPLY_OBJECT["anyOf"][1],
            "properties": {
                **ACT_OR_ASK["properties"],
                "parameters": {"type": "object", "additionalProperties": True, "properties": {}},
            },
        },
    ],
}

USER_MESSAGE = {"role": "user", "content": PROMPT}


def _object_schema(info: AgentInfo) -> dict[str, Any]:
    """The schema of the reply a request asked for, from wherever its shape put it."""
    if info.output_tools:
        return info.output_tools[0].parameters_json_schema
    output = info.model_request_parameters.output_object
    assert output is not None, "the request carries no output schema"
    return output.json_schema


def _values(schema: dict[str, Any]) -> list[str]:
    """What a field's schema allows: its enum, or its one constant."""
    allowed: list[str] = schema["enum"] if "enum" in schema else [schema["const"]]
    return allowed


def test_the_shapes_are_the_ones_the_setting_allows() -> None:
    assert REFLEX_OUTPUTS == ("text", "tool", "native", "json")
    assert set(STRUCTURED) == set(REFLEX_OUTPUTS) - {"text"}


@pytest.mark.parametrize("shape", REFLEX_OUTPUTS)
def test_the_agent_carries_its_shape(shape: ReflexOutput) -> None:
    agent, _ = reflex_agent(output=shape)

    assert agent.output_shape == shape


def test_the_default_shape_is_text() -> None:
    agent, _ = reflex_agent()

    assert agent.output_shape == "text"


@pytest.mark.parametrize("shape", STRUCTURED)
async def test_a_run_that_bypasses_decide_asks_for_no_schema_of_its_own(
    shape: ReflexOutput,
) -> None:
    """The shape is asked for by ``decide``, per call: the agent alone is the text agent."""
    agent, script = reflex_agent(ASK, output=shape)

    run = await agent.run(PROMPT, deps=TOOLS)

    assert run.output.decision == "ask"
    assert script.requests[0][1].output_tools == []
    assert script.requests[0][1].model_request_parameters.output_object is None


@pytest.mark.parametrize("shape", STRUCTURED)
async def test_a_reply_in_each_shape_is_a_proposal(shape: ReflexOutput) -> None:
    agent, script = reflex_agent(ASK_OBJECT, output=shape)

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "ask"
    assert proposal.reason == "dark"
    assert proposal.action is not None
    assert proposal.action.source == "reflex-engine"
    assert proposal.action.target_service == "home-service"
    assert proposal.action.tool_name == "home.light_turn_on"
    assert proposal.action.parameters == {"target": "Lamp"}
    assert script.call_count == 1


@pytest.mark.parametrize("shape", STRUCTURED)
async def test_a_none_reply_in_each_shape_is_none(shape: ReflexOutput) -> None:
    agent, _ = reflex_agent(NONE_OBJECT, output=shape)

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "none"
    assert proposal.action is None


@pytest.mark.parametrize("shape", [*STRUCTURED, "text"])
async def test_a_tool_that_is_not_one_of_the_calls_tools_is_invalid_in_every_shape(
    shape: ReflexOutput,
) -> None:
    reply: Any = ASK if shape == "text" else ASK_OBJECT
    agent, script = reflex_agent(reply, output=shape)

    proposal = await decide(agent, PROMPT, [COVER])

    assert proposal.decision == "invalid"
    assert proposal.action is None
    # The same check, whatever the shape: one conversion after the JSON.
    assert proposal.problem == "tool 'home.light_turn_on' is not one of Reflex's tools"
    assert json.loads(proposal.raw or "") == json.loads(ASK)
    assert script.call_count == 1


def test_the_structured_schema_is_the_text_shapes_reply_with_two_open_ends() -> None:
    assert agent_module.reply_schema(TOOLS) == REPLY_OBJECT
    assert agent_module.reply_schema([]) == NONE_REPLY  # nothing to open: only "none"
    assert reply_format(TOOLS)["json_schema"]["schema"] == REPLY_FORMAT["json_schema"]["schema"]


def test_the_schema_of_one_call_does_not_leak_into_the_next() -> None:
    first = agent_module.reply_schema(TOOLS)
    first["anyOf"].clear()

    assert agent_module.reply_schema(TOOLS) == REPLY_OBJECT
    assert reply_format(TOOLS) == REPLY_FORMAT


def test_the_vllm_profile_leaves_the_reply_satisfiable(upstream: Upstream) -> None:
    """Its transformer closes every object that does not say it is open: the two ends must."""
    transformer = _vllm(upstream).profile.get("json_schema_transformer")
    assert transformer is not None

    sent = transformer(copy.deepcopy(agent_module.reply_schema(TOOLS)), strict=None).walk()
    closed = transformer(
        {"type": "object", "anyOf": [NONE_REPLY, ACT_OR_ASK]}, strict=None
    ).walk()  # the anyOf with no ends spelled out

    assert sent == WIRE_REPLY_OBJECT
    assert closed["additionalProperties"] is False  # no key could be written
    assert closed["anyOf"][1]["properties"]["parameters"]["additionalProperties"] is False


@pytest.mark.parametrize("shape", STRUCTURED)
async def test_with_no_tools_the_only_reply_is_none_in_every_shape(shape: ReflexOutput) -> None:
    agent, script = reflex_agent(ASK_OBJECT, NONE_OBJECT, output=shape)

    asked = await decide(agent, PROMPT, [])
    none = await decide(agent, PROMPT, [])

    assert asked.decision == "invalid"
    assert none.decision == "none"
    schema = _object_schema(script.requests[0][1])
    assert _values(schema["properties"]["decision"]) == ["none"]
    assert schema["required"] == ["decision"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("shape", STRUCTURED)
async def test_the_reply_must_name_one_of_the_tools_of_this_call_in_every_shape(
    shape: ReflexOutput,
) -> None:
    """The enum and the required fields are the text shape's, so no shape is asked for less."""
    agent, script = reflex_agent(NONE_OBJECT, NONE_OBJECT, output=shape)

    await decide(agent, PROMPT, [*TOOLS, COVER])
    await decide(agent, PROMPT, TOOLS)

    first, second = (_object_schema(script.requests[i][1])["anyOf"] for i in (0, 1))
    for none, choose in (first, second):
        assert none == NONE_REPLY
        assert choose["required"] == ["decision", "reason", "tool_name", "parameters"]
        assert choose["additionalProperties"] is False
        assert _values(choose["properties"]["decision"]) == ["act", "ask"]
    assert first[1]["properties"]["tool_name"] == {
        "enum": ["home.cover_open", "home.light_turn_on"]
    }
    assert second[1]["properties"]["tool_name"] == {"enum": ["home.light_turn_on"]}


@pytest.mark.parametrize("shape", STRUCTURED)
async def test_only_the_text_shape_sends_the_response_format(shape: ReflexOutput) -> None:
    """The other shapes get their schema from Pydantic AI's output machinery, once."""
    agent, script = reflex_agent(NONE_OBJECT, output=shape)

    await decide(agent, PROMPT, TOOLS)

    settings = script.settings()
    assert "extra_body" not in settings
    assert settings["temperature"] == 0.0
    assert settings["max_tokens"] == 150
    assert settings["timeout"] == 30.0
    assert settings["extra_headers"] == {"X-Alfred-Role": "system1"}


# --- a structured reply that is no reply ----------------------------------------------

PRIVATE = "private words"


@pytest.mark.parametrize(
    ("shape", "reply", "raw", "problem"),
    [
        pytest.param(
            "native",
            PRIVATE,
            PRIVATE,
            "no reply: Exceeded maximum output retries (0) (reply: json_invalid)",
            id="native-not-json",
        ),
        pytest.param(
            "json",
            PRIVATE,
            PRIVATE,
            "no reply: Exceeded maximum output retries (0) (reply: json_invalid)",
            id="json-not-json",
        ),
        pytest.param(
            "native",
            '{"decision": "act", "reason": "da',
            '{"decision": "act", "reason": "da',
            "no reply: Exceeded maximum output retries (0) (reply: json_invalid)",
            id="native-cut-off-at-the-cap",
        ),
        pytest.param(
            "json",
            f'["{PRIVATE}"]',
            f'["{PRIVATE}"]',
            "no reply: Exceeded maximum output retries (0) (reply: dict_type)",
            id="json-not-an-object",
        ),
        pytest.param(
            "tool",
            tool_call("reflex_reply", PRIVATE),
            PRIVATE,
            "no reply: Exceeded maximum output retries (0) (reply: json_invalid)",
            id="tool-arguments-not-json",
        ),
        pytest.param(
            "tool",
            tool_call("reflex_reply", f'["{PRIVATE}"]'),
            f'["{PRIVATE}"]',
            "no reply: Exceeded maximum output retries (0) (reply: dict_type)",
            id="tool-arguments-not-an-object",
        ),
        pytest.param(
            "tool",
            PRIVATE,
            PRIVATE,
            "no reply: Exceeded maximum output retries (0)",
            id="tool-no-tool-call",
        ),
    ],
)
async def test_a_structured_reply_that_is_no_reply_is_invalid_keeps_the_raw_reply_and_says_why(
    shape: ReflexOutput, reply: Any, raw: str, problem: str
) -> None:
    agent, script = reflex_agent(reply, output=shape)

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.raw == raw
    assert proposal.problem == problem
    assert PRIVATE not in (proposal.problem or "")  # the runner logs it; the reply is private
    assert script.call_count == 1


@pytest.mark.parametrize("shape", STRUCTURED)
async def test_a_reply_the_schema_does_not_police_is_checked_after_the_json(
    shape: ReflexOutput,
) -> None:
    """The server holds the reply to the schema; a server that does not is caught here."""
    agent, script = reflex_agent({"decision": "maybe"}, output=shape)

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.problem == "unknown decision 'maybe'"
    assert json.loads(proposal.raw or "") == {"decision": "maybe"}
    assert script.call_count == 1


@pytest.mark.parametrize("shape", STRUCTURED)
async def test_a_reply_the_schema_allows_is_still_checked_and_keeps_the_reply_as_raw(
    shape: ReflexOutput,
) -> None:
    agent, _ = reflex_agent({"decision": "act", "reason": "dark"}, output=shape)

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.problem == "act without a tool"
    assert json.loads(proposal.raw or "") == {"decision": "act", "reason": "dark"}


async def test_an_empty_text_reply_keeps_an_empty_raw_reply() -> None:
    agent, _ = reflex_agent(ModelResponse(parts=[]), output="native")

    proposal = await decide(agent, PROMPT, TOOLS)

    assert proposal.decision == "invalid"
    assert proposal.raw == ""
    assert (proposal.problem or "").startswith("no reply: ")


# --- the other shapes, on the wire ----------------------------------------------------


def _tool_call_message(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "reflex_reply", "arguments": json.dumps(arguments)},
            }
        ],
    }


def _body_schema(body: dict[str, Any], shape: ReflexOutput, upstream: Upstream) -> dict[str, Any]:
    """The schema a request carries, read from wherever its shape put it."""
    schema: dict[str, Any]
    if shape == "tool":
        [tool] = body["tools"]
        schema = tool["function"]["parameters"]
    elif shape == "native":
        schema = body["response_format"]["json_schema"]["schema"]
    else:
        # The system message is the profile's own template around the schema: read the schema
        # out of it by that template, so a change of its wording does not break the test.
        before, _, after = _vllm(upstream).profile["prompted_output_template"].partition("{schema}")
        [system, _] = body["messages"]
        assert system["role"] == "system"
        assert system["content"].startswith(before)
        assert system["content"].endswith(after)
        schema = json.loads(system["content"].removeprefix(before).removesuffix(after))
    return schema


def _body(**shape: Any) -> dict[str, Any]:
    """What every structured request carries besides its shape: the settings, the prompt."""
    return {
        "model": "test-model",
        "stream": False,
        "max_completion_tokens": 150,
        "temperature": 0.0,
        **shape,
    }


async def test_the_tool_shape_is_pinned_on_the_wire(upstream: Upstream) -> None:
    upstream.message = _tool_call_message(NONE_OBJECT)

    await decide(build_reflex_agent(_vllm(upstream), "tool"), PROMPT, TOOLS)

    assert upstream.headers["x-alfred-role"] == "system1"
    assert upstream.body == _body(
        messages=[USER_MESSAGE],
        tool_choice="required",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "reflex_reply",
                    "description": "The final response which ends this conversation",
                    "parameters": WIRE_REPLY_OBJECT,
                },
            }
        ],
    )


async def test_the_native_shape_is_pinned_on_the_wire_and_sends_the_schema_once(
    upstream: Upstream,
) -> None:
    """The vLLM profile would add the schema to a system message too: the server already holds
    the reply to it, as it does for ``text``, so the prompt is the only message."""
    await decide(build_reflex_agent(_vllm(upstream), "native"), PROMPT, TOOLS)

    assert upstream.headers["x-alfred-role"] == "system1"
    assert upstream.body == _body(
        messages=[USER_MESSAGE],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "reflex_reply", "schema": WIRE_REPLY_OBJECT, "strict": False},
        },
    )


async def test_the_json_shape_is_pinned_on_the_wire_with_the_schema_in_the_prompt(
    upstream: Upstream,
) -> None:
    await decide(build_reflex_agent(_vllm(upstream), "json"), PROMPT, TOOLS)

    body = upstream.body
    [system, _] = body["messages"]
    assert _body_schema(body, "json", upstream) == {**WIRE_REPLY_OBJECT, "title": "reflex_reply"}
    assert upstream.headers["x-alfred-role"] == "system1"
    assert body == _body(messages=[system, USER_MESSAGE], response_format={"type": "json_object"})


@pytest.mark.parametrize("shape", STRUCTURED)
async def test_each_shape_names_the_tools_of_its_own_call_on_the_wire(
    shape: ReflexOutput, upstream: Upstream
) -> None:
    if shape == "tool":
        upstream.message = _tool_call_message(NONE_OBJECT)

    await decide(build_reflex_agent(_vllm(upstream), shape), PROMPT, [*TOOLS, COVER])

    [_, choose] = _body_schema(upstream.body, shape, upstream)["anyOf"]
    assert choose["properties"]["tool_name"] == {"enum": ["home.cover_open", "home.light_turn_on"]}
    assert choose["required"] == ["decision", "reason", "tool_name", "parameters"]


async def test_the_text_shape_is_the_default_and_sends_the_same_bytes(
    upstream: Upstream,
) -> None:
    await decide(build_reflex_agent(_vllm(upstream)), PROMPT, TOOLS)
    await decide(build_reflex_agent(_vllm(upstream), "text"), PROMPT, TOOLS)

    first, second = upstream.requests
    assert first.content == second.content
    assert json.loads(first.content)["response_format"] == REPLY_FORMAT
