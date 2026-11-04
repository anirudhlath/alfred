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
response format, or a schema in the prompt. Each keeps the per-call enum of tool names, as
a reply model built for the call from the tools (``reply_model``) and passed to that run
alone, and each ends in ``proposal_from_parsed``, the checks ``parse_decision`` makes after
the JSON. Only ``text`` sends ``reply_format``.

The agent is built once per process. Its dependencies are the tools the prompt showed, which
the reply is validated against.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, Field, create_model
from pydantic_ai import (
    Agent,
    NativeOutput,
    PromptedOutput,
    RunContext,
    TextOutput,
    ToolOutput,
)
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings

from bus.schemas.events import ReflexProposal
from core.reflex.decision import parse_decision, proposal_from_parsed
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.telemetry import track_tokens
from shared.agents import instrumentation, role_headers
from shared.config import DEFAULT_REFLEX_OUTPUT
from shared.llm_roles import SYSTEM1

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.output import OutputSpec
    from pydantic_ai.usage import RunUsage

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

# The structured shapes, by the marker Pydantic AI asks for each with.
OutputMarker = type[ToolOutput[Any]] | type[NativeOutput[Any]] | type[PromptedOutput[Any]]
OUTPUT_SHAPES: Final[dict[ReflexOutput, OutputMarker]] = {
    "tool": ToolOutput,
    "native": NativeOutput,
    "json": PromptedOutput,
}

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


class ReflexReply(BaseModel):
    """The reply the ``reflex-rules`` prompt asks for, as the structured shapes read it.

    Field for field the reply line: ``{"decision": "none"}``, or an act or ask with its
    reason, tool and parameters, in the order the line and ``reply_format`` give them. A
    model does not say which fields each decision needs, so only ``decision`` is required:
    an act or ask that leaves out its tool is made ``invalid`` by the checks that follow
    (``proposal_from_parsed``), with the reply kept. ``tool_name`` is any string here;
    ``reply_model`` narrows it to the tools of one call, as ``reply_format`` does.
    """

    decision: Literal["none", "act", "ask"]
    reason: str | None = None
    tool_name: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


def reply_model(tools: list[ToolInfo]) -> type[ReflexReply]:
    """``ReflexReply`` for a call that showed *tools*: ``tool_name`` is one of them.

    The same tools give the same class, so the schema the server sees is stable and it can
    reuse what it built. With no tools the only reply is "none".
    """
    return _reply_model(tool_names(tools))


@lru_cache(maxsize=32)
def _reply_model(names: tuple[str, ...]) -> type[ReflexReply]:
    if not names:
        return create_model(
            "ReflexReply",
            __base__=ReflexReply,
            decision=(Literal["none"], ...),
            tool_name=(None, None),
        )
    # Literal[names] with names only known at run time: the subscript spelled out, typed Any.
    one_of: Any = Literal.__getitem__(names)
    return create_model("ReflexReply", __base__=ReflexReply, tool_name=(one_of | None, None))


def proposal_from_reply(reply: ReflexReply, tools: list[ToolInfo]) -> ReflexProposal:
    """The proposal for a structured reply: ``parse_decision``'s checks on the reply's fields.

    A proposal that fails them keeps the reply as the model wrote it, the fields it set.
    """
    return proposal_from_parsed(
        reply.model_dump(), reply.model_dump_json(exclude_unset=True), tools
    )


async def _parse(ctx: RunContext[list[ToolInfo]], text: str) -> ReflexProposal:
    # Async so it runs on the event loop: Pydantic AI runs a sync output function in a
    # worker thread, a hop on every decision for a parse that never blocks.
    return parse_decision(text, ctx.deps)


async def _proposal(ctx: RunContext[list[ToolInfo]], reply: ReflexReply) -> ReflexProposal:
    return proposal_from_reply(reply, ctx.deps)


def build_reflex_agent(
    model: Model, output: ReflexOutput = DEFAULT_REFLEX_OUTPUT
) -> Agent[list[ToolInfo], ReflexProposal]:
    """The agent that decides with *model*, asking for its reply in the *output* shape.

    No retries: Pydantic AI would otherwise ask again after an empty reply, and Reflex has
    one chance inside its latency budget. ``instrumentation()`` reads the tracer provider,
    so call this after ``init_tracing``.

    A structured shape's output type here names the shape and makes the agent whole on its
    own, but its ``tool_name`` takes any string: ``infer`` replaces it, for every run, with
    the same shape over ``reply_model(tools)``.
    """
    output_type: OutputSpec[ReflexProposal] = (
        TextOutput[ReflexProposal](_parse)
        if output == "text"
        else OUTPUT_SHAPES[output](_proposal, name=REPLY_FORMAT_NAME)
    )
    return Agent(
        model,
        name=AGENT_NAME,
        deps_type=list[ToolInfo],
        output_type=output_type,
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


@track_tokens(model=TOKENS_MODEL_LABEL)
async def infer(
    agent: Agent[list[ToolInfo], ReflexProposal], prompt: str, tools: list[ToolInfo]
) -> dict[str, Any]:
    """One run, with the token counts ``track_tokens`` records and the proposal it made.

    ``track_tokens`` only reads a returned dict, so the counts travel with the proposal.
    The name is the research series' ``function`` column. What differs by call goes in
    the run: the ``text`` shape's response format in its settings (it has no ``ModelSettings``
    field, and ``extra_body`` lands in the request as written; the agent sets no
    ``extra_body`` of its own, since the run's would replace it whole), and a structured
    shape's reply model as the run's output type.
    """
    proposal, usage = await _run(agent, prompt, tools)
    return {
        "proposal": proposal,
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


async def _run(
    agent: Agent[list[ToolInfo], ReflexProposal], prompt: str, tools: list[ToolInfo]
) -> tuple[ReflexProposal, RunUsage]:
    marker = next(
        (marker for marker in OUTPUT_SHAPES.values() if isinstance(agent.output_type, marker)),
        None,
    )
    if marker is None:  # the text shape
        format_settings = ModelSettings(extra_body={"response_format": reply_format(tools)})
        text_run = await agent.run(prompt, deps=tools, model_settings=format_settings)
        return text_run.output, text_run.usage
    output_type = marker(reply_model(tools), name=REPLY_FORMAT_NAME)
    run = await agent.run(prompt, deps=tools, output_type=output_type)
    reply: ReflexReply = run.output
    return proposal_from_reply(reply, tools), run.usage


async def decide(
    agent: Agent[list[ToolInfo], ReflexProposal], prompt: str, tools: list[ToolInfo]
) -> ReflexProposal:
    """The proposal *agent* makes for *prompt*; *tools* are the ones the prompt showed.

    A transport or provider failure propagates, so the runner leaves the entry pending and
    the reclaim pass replays it. Before it propagates, the retrying transport
    (``shared/llm_http.py``) may try the request up to 3 times, retrying only within 30 s of
    the first try, each try bounded by ``REQUEST_TIMEOUT_S``. Reflex decides one event at a
    time, so a server outage serialises its loop behind those tries: accepted while Reflex
    runs in shadow.

    ``UnexpectedModelBehavior`` is an ``invalid`` proposal, ACKed like any other, with the
    exception's message in ``problem``. That covers a reply with nothing in it (an empty
    message, or all of the token cap spent before any text), a malformed body from the
    server and a content-filter stop. Narrow it when Reflex acts: a malformed body is worth
    a retry then.
    """
    try:
        outcome = await infer(agent, prompt, tools)
    except UnexpectedModelBehavior as exc:
        return ReflexProposal(decision="invalid", raw="", problem=f"no reply: {exc.message}")
    proposal: ReflexProposal = outcome["proposal"]
    return proposal
