"""Reflex's model call: one Pydantic AI run from a prompt to a ``ReflexProposal``.

The agent has no instructions. The prompt ``core.reflex.prompt`` builds, rules and all, is
the single user message, and the reply text goes through ``parse_decision``, so a reply
that is not a decision is an ``invalid`` proposal that keeps the raw text, never a retry.
Every request carries temperature 0, a 150-token cap, the ``system1`` role header and a
response format built for that call from the tools the prompt showed, so the server only lets
the model write a reply that is "none" or names one of them (``tests/core/reflex/test_agent.py``
pins it on the wire). ``parse_decision`` still checks the reply: a server that ignores the
format sends whatever the model wrote.

That is the ``text`` output shape, the one that runs in production. The others (``tool``,
``native``, ``json``) ask for the same reply through Pydantic AI's structured output instead,
so candidates can be compared by their counts: an output tool, the server's JSON-schema
response format, or a schema in the prompt. Each sends ``reply_format``'s own schema, the
same anyOf with the same tool enum and the same required fields, built for the call from
its tools (``reply_schema``) and passed to that run alone, and each ends in
``proposal_from_parsed``, the checks ``parse_decision`` makes after the JSON. The agent
carries the shape it was built for (``ReflexAgent.output_shape``); only ``text`` sends
``reply_format``.

The agent is built once per process. Its dependencies are the tools the prompt showed, which
the reply is validated against.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any, Final, assert_never

from pydantic import ValidationError
from pydantic_ai import (
    Agent,
    NativeOutput,
    PromptedOutput,
    RunContext,
    StructuredDict,
    TextOutput,
    ToolOutput,
    capture_run_messages,
)
from pydantic_ai.exceptions import ToolRetryError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse
from pydantic_ai.settings import ModelSettings

from bus.schemas.events import ReflexProposal
from core.reflex.decision import parse_decision, proposal_from_parsed
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.telemetry import track_tokens
from shared.agents import instrumentation, role_headers
from shared.config import DEFAULT_REFLEX_OUTPUT
from shared.llm_roles import SYSTEM1

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.usage import RunUsage
    from pydantic_core import ErrorDetails

    from shared.config import ReflexOutput

AGENT_NAME: Final = "reflex"

# Reflex replies are a short JSON object: ~7 tokens for "none", ~40 with a reason.
# The cap bounds the worst case at ~0.9 s of decode time (#285).
MAX_OUTPUT_TOKENS: Final = 150

# How long one request may take: the timeout of the client this agent replaced. Without it
# Pydantic AI's default (ten minutes) would stall the consumer loop behind a hung server.
REQUEST_TIMEOUT_S: Final = 30.0

# The label of the research token series, kept from the client this agent replaced so the
# series continues (`.claude/rules/research-protocol.md`).
TOKENS_MODEL_LABEL: Final = "openai-compat"

# The name every shape gives its schema (the response format's, the output tool's).
REPLY_FORMAT_NAME: Final = "reflex_reply"

_NONE_REPLY: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {"decision": {"const": "none"}},
    "required": ["decision"],
    "additionalProperties": False,
}


def reply_format(tools: list[ToolInfo]) -> dict[str, Any]:
    """The response format for a call that showed *tools*: the reply the rules prompt asks for.

    That is ``{"decision": "none"}``, or an act or ask whose ``tool_name`` is one of *tools*.
    With no tools the only reply is "none". The names are sorted, as the prompt lists them,
    so the same tools give the same schema and the server can reuse what it built for it.
    Without the enum the model sometimes wrote a tool it had not been shown (#317).

    The reply line of the ``reflex-rules`` prompt describes the same reply: change the two
    together, because the server enforces this schema whatever the prompt asks for.
    """
    if not tools:
        return _json_schema(_NONE_REPLY)
    act_or_ask = {
        "type": "object",
        "properties": {
            "decision": {"enum": ["act", "ask"]},
            "reason": {"type": "string"},
            "tool_name": {"enum": list(tool_names(tools))},
            "parameters": {"type": "object"},
        },
        "required": ["decision", "reason", "tool_name", "parameters"],
        "additionalProperties": False,
    }
    return _json_schema({"anyOf": [_NONE_REPLY, act_or_ask]})


def _json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": REPLY_FORMAT_NAME, "schema": schema}}


def tool_names(tools: list[ToolInfo]) -> tuple[str, ...]:
    """The names a reply may give, sorted as the prompt lists them: the one enum of every shape."""
    return tuple(sorted({tool.name for tool in tools}))


def reply_schema(tools: list[ToolInfo]) -> dict[str, Any]:
    """The schema inside ``reply_format(tools)``, as the structured shapes send it.

    The same reply, so no shape is asked for less than ``text``: the anyOf keeps every
    branch, enum and required field. Pydantic AI wants an object at the top, so the anyOf
    gets ``type: object``, and ``additionalProperties: true`` is spelled out at the top and
    on ``parameters``. The vLLM profile closes every object that does not say it is open,
    which would leave the top level no key to write and ``parameters`` none to hold
    (``tests/core/reflex/test_agent.py`` pins what the profile sends). The branches still
    decide which keys a reply may have. With no tools there is one branch and nothing to open.
    """
    schema: dict[str, Any] = copy.deepcopy(reply_format(tools)["json_schema"]["schema"])
    if "anyOf" in schema:
        schema["type"] = "object"
        schema["additionalProperties"] = True
        for branch in schema["anyOf"]:
            if (parameters := branch["properties"].get("parameters")) is not None:
                parameters["additionalProperties"] = True
    return schema


async def _parse(ctx: RunContext[list[ToolInfo]], text: str) -> ReflexProposal:
    # Async so it runs on the event loop: Pydantic AI runs a sync output function in a
    # worker thread, a hop on every decision for a parse that never blocks.
    return parse_decision(text, ctx.deps)


class ReflexAgent(Agent[list[ToolInfo], ReflexProposal]):
    """The agent that decides, and the output shape it asks for its reply in.

    On its own it reads the reply as text, whatever its shape: ``decide`` is what asks in the
    shape (``output_shape``), for every run, with a schema built from that call's tools.
    """

    output_shape: ReflexOutput

    def __init__(self, model: Model, output: ReflexOutput = DEFAULT_REFLEX_OUTPUT) -> None:
        super().__init__(
            model,
            name=AGENT_NAME,
            deps_type=list[ToolInfo],
            output_type=TextOutput[ReflexProposal](_parse),
            retries=0,
            model_settings=ModelSettings(
                temperature=0.0,
                max_tokens=MAX_OUTPUT_TOKENS,
                timeout=REQUEST_TIMEOUT_S,
                extra_headers=role_headers(SYSTEM1),
            ),
            capabilities=[instrumentation()],
            defer_model_check=True,
        )
        self.output_shape = output


def build_reflex_agent(model: Model, output: ReflexOutput = DEFAULT_REFLEX_OUTPUT) -> ReflexAgent:
    """The agent that decides with *model*, asking for its reply in the *output* shape.

    No retries: Pydantic AI would otherwise ask again after an empty reply, and Reflex has
    one chance inside its latency budget. ``instrumentation()`` reads the tracer provider,
    so call this after ``init_tracing``.
    """
    return ReflexAgent(model, output)


def _structured_output(
    shape: ReflexOutput, tools: list[ToolInfo]
) -> ToolOutput[dict[str, Any]] | NativeOutput[dict[str, Any]] | PromptedOutput[dict[str, Any]]:
    """What a structured *shape* asks for in a call that showed *tools*: ``reply_schema``.

    A ``StructuredDict`` reads the reply as an object and nothing more, so every other check
    is ``proposal_from_parsed``'s, the same one ``text`` makes. ``text`` is not structured: it
    sends ``reply_format`` instead, so asking for its output is an error, and so is a shape
    this function does not know.
    """
    reply = StructuredDict(reply_schema(tools), name=REPLY_FORMAT_NAME)
    match shape:
        case "tool":
            return ToolOutput(reply, name=REPLY_FORMAT_NAME)
        case "native":
            # The vLLM profile would also put the schema in a system message. The server
            # already holds the reply to it, as it does for ``text``, so the request carries
            # it once.
            return NativeOutput(reply, name=REPLY_FORMAT_NAME, template=False)
        case "json":
            return PromptedOutput(reply, name=REPLY_FORMAT_NAME)
        case "text":
            raise ValueError("the text shape sends reply_format and has no structured output")
        case _:
            assert_never(shape)


@track_tokens(model=TOKENS_MODEL_LABEL)
async def infer(agent: ReflexAgent, prompt: str, tools: list[ToolInfo]) -> dict[str, Any]:
    """One run, with the token counts ``track_tokens`` records and the proposal it made.

    ``track_tokens`` only reads a returned dict, so the counts travel with the proposal.
    The name is the research series' ``function`` column. What differs by call goes in
    the run: the ``text`` shape's response format in its settings (it has no ``ModelSettings``
    field, and ``extra_body`` lands in the request as written; the agent sets no
    ``extra_body`` of its own, since the run's would replace it whole), and a structured
    shape's schema as the run's output type.
    """
    proposal, usage = await _run(agent, prompt, tools)
    return {
        "proposal": proposal,
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


async def _run(
    agent: ReflexAgent, prompt: str, tools: list[ToolInfo]
) -> tuple[ReflexProposal, RunUsage]:
    if agent.output_shape == "text":
        format_settings = ModelSettings(extra_body={"response_format": reply_format(tools)})
        text_run = await agent.run(prompt, deps=tools, model_settings=format_settings)
        return text_run.output, text_run.usage
    run = await agent.run(
        prompt, deps=tools, output_type=_structured_output(agent.output_shape, tools)
    )
    return proposal_from_parsed(run.output, json.dumps(run.output), tools), run.usage


def _reply_so_far(messages: list[ModelMessage]) -> str:
    """What the model's last response said: its text, or else a tool call's arguments."""
    last = next((m for m in reversed(messages) if isinstance(m, ModelResponse)), None)
    if last is None:
        return ""
    if last.text:
        return last.text
    call = next(iter(last.tool_calls), None)
    if call is None:
        return ""
    # A string is what the server sent, word for word; Pydantic AI would wrap one that is not JSON.
    return call.args if isinstance(call.args, str) else call.args_as_json_str()


def _errors(cause: BaseException | None) -> list[ErrorDetails]:
    """The errors Pydantic found in a reply, when that is why a run raised."""
    if isinstance(cause, ValidationError):
        return cause.errors(include_input=False)
    if isinstance(cause, ToolRetryError) and isinstance(cause.tool_retry.content, list):
        return cause.tool_retry.content
    return []


def _no_reply(exc: UnexpectedModelBehavior, messages: list[ModelMessage]) -> ReflexProposal:
    """The ``invalid`` proposal for a run that ended with no usable reply.

    ``raw`` is the model's last response, as the ``text`` shape keeps its reply. ``problem``
    is logged at WARNING, so it says where and what kind of error Pydantic found (``loc`` and
    ``type``) and never the reply, which a validation error carries as its ``input``.
    """
    kinds = ", ".join(
        f"{'.'.join(map(str, error['loc'])) or 'reply'}: {error['type']}"
        for error in _errors(exc.__cause__)
    )
    problem = f"no reply: {exc.message}" + (f" ({kinds})" if kinds else "")
    return ReflexProposal(decision="invalid", raw=_reply_so_far(messages), problem=problem)


async def decide(agent: ReflexAgent, prompt: str, tools: list[ToolInfo]) -> ReflexProposal:
    """The proposal *agent* makes for *prompt*; *tools* are the ones the prompt showed.

    A transport or provider failure propagates, so the runner leaves the entry pending and
    the reclaim pass replays it. Before it propagates, the retrying transport
    (``shared/llm_http.py``) may try the request up to 3 times, retrying only within 30 s of
    the first try, each try bounded by ``REQUEST_TIMEOUT_S``. Reflex decides one event at a
    time, so a server outage serialises its loop behind those tries: accepted while Reflex
    runs in shadow.

    ``UnexpectedModelBehavior`` is an ``invalid`` proposal, ACKed like any other, with
    ``no reply:``, the exception's message and the kind of each error Pydantic found in
    ``problem``, and the model's last response in ``raw``. That covers a reply with nothing
    in it (an empty message, or all of the token cap spent before any text), a malformed body
    from the server, a content-filter stop and, in a structured shape, a reply that is not an
    object (not JSON, cut off at the token cap, text where a tool call was required). A reply
    that is an object but breaks the schema raises nothing: ``proposal_from_parsed`` makes it
    ``invalid`` with its own ``problem``, as for ``text``. Narrow it when Reflex acts: a
    malformed body is worth a retry then.
    """
    with capture_run_messages() as messages:
        try:
            outcome = await infer(agent, prompt, tools)
        except UnexpectedModelBehavior as exc:
            return _no_reply(exc, messages)
    proposal: ReflexProposal = outcome["proposal"]
    return proposal
