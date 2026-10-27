"""Reflex's model call: one Pydantic AI run from a prompt to a ``ReflexProposal``.

The agent has no instructions. The prompt ``core.reflex.prompt`` builds, rules and all, is
the single user message, and the reply text goes through ``parse_decision``, so a reply
that is not a decision is an ``invalid`` proposal that keeps the raw text, never a retry.
The request is the one Reflex has always sent: temperature 0, a 150-token cap, a
JSON-object response format and the ``system1`` role header (``tests/core/reflex/test_agent.py``
pins it on the wire).

The agent is built once per process. Its dependencies are the tools the prompt showed, which
``parse_decision`` validates the reply against.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic_ai import Agent, RunContext, TextOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings

from bus.schemas.events import ReflexProposal
from core.reflex.decision import parse_decision
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.telemetry import track_tokens
from shared.agents import instrumentation, role_headers
from shared.llm_roles import SYSTEM1

if TYPE_CHECKING:
    from pydantic_ai.models import Model

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


async def _parse(ctx: RunContext[list[ToolInfo]], text: str) -> ReflexProposal:
    # Async so it runs on the event loop: Pydantic AI runs a sync output function in a
    # worker thread, a hop on every decision for a parse that never blocks.
    return parse_decision(text, ctx.deps)


def build_reflex_agent(model: Model) -> Agent[list[ToolInfo], ReflexProposal]:
    """The agent that decides with *model*.

    No retries: Pydantic AI would otherwise ask again after an empty reply, and Reflex has
    one chance inside its latency budget. ``instrumentation()`` reads the tracer provider,
    so call this after ``init_tracing``.
    """
    return Agent(
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
            # No ModelSettings field for it; extra_body lands in the request as written.
            extra_body={"response_format": {"type": "json_object"}},
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
    The name is the research series' ``function`` column.
    """
    result = await agent.run(prompt, deps=tools)
    usage = result.usage
    return {
        "proposal": result.output,
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


async def decide(
    agent: Agent[list[ToolInfo], ReflexProposal], prompt: str, tools: list[ToolInfo]
) -> ReflexProposal:
    """The proposal *agent* makes for *prompt*; *tools* are the ones the prompt showed.

    A transport or provider failure propagates, so the runner leaves the entry pending and
    the reclaim pass replays it. A reply with nothing in it (an empty message, or all of
    the token cap spent before any text) is an ``invalid`` proposal like any other reply
    that is not a decision.
    """
    try:
        outcome = await infer(agent, prompt, tools)
    except UnexpectedModelBehavior as exc:
        return ReflexProposal(decision="invalid", raw="", problem=f"no reply: {exc.message}")
    proposal: ReflexProposal = outcome["proposal"]
    return proposal
