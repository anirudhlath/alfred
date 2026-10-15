"""The Librarian's LLM calls: five small agents, each with a typed output.

Built once per process from ``LIBRARIAN_MODEL`` (``build_librarian_agents``) and run
per consolidation pass through ``LibrarianAgents.ask``, which caps the response and
carries the Librarian role header. Every agent carries the process's ``SpendLimits``
(``shared.spend``), which counts each model request and refuses one once the day's
budget is spent (``SpendLimitExceeded``, left for the caller to stop its pass on).

The instructions come from the prompt store (``shared.prompts``) and say what to return.
How to shape it is the output schema's job: it travels with every request, as JSON-schema
output where the model supports it and as an output tool where it does not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext
from pydantic_ai.settings import ModelSettings

from shared.agents import instrumentation, role_headers
from shared.config import DEFAULT_SYSTEM2_TIMEOUT_S
from shared.llm_roles import LIBRARIAN
from shared.prompts.link import linked_prompts

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.models import Model
    from pydantic_ai.output import OutputSpec
    from pydantic_ai_harness.spend import SpendLimits

    from shared.prompts.template import RenderedPrompt

logger = logging.getLogger(__name__)

# One more try when the output fails validation (a wrong count, a malformed object);
# after that the caller takes its fallback.
OUTPUT_RETRIES: Final = 1


class Significance(BaseModel):
    """How much an observation matters, per dimension, from 0.0 to 1.0."""

    safety: float = Field(0.0, description="1.0 for emergencies and alarms")
    novelty: float = Field(0.0, description="1.0 for a first-time event")
    personal: float = Field(0.0, description="1.0 for a direct interaction with the user")
    emotional: float = Field(0.0, description="1.0 for strong sentiment")


class ObservationAnalysis(BaseModel):
    """One observation's entities, significance and search phrase."""

    entities: list[str] = Field(
        default_factory=list, description="Entity ids, room names or people mentioned"
    )
    significance: Significance = Field(default_factory=Significance)
    semantic_key: str = Field(
        "", description="Short human-readable phrase summarising the core meaning"
    )


class PreferenceUpdate(BaseModel):
    """A user preference read from the observations."""

    domain: str = Field(description="The area it concerns, e.g. climate, lighting, food")
    observation: str = Field(description="The preference, stated plainly")


class ConflictItem(BaseModel):
    """How one new observation relates to the learned preferences."""

    type: Literal["confirm", "contradict", "new"]
    line: str = Field("", description="confirm: the existing preference's text")
    old: str = Field("", description="contradict: the existing preference's text")
    new: str = Field("", description="contradict: the revised preference")
    content: str = Field("", description="new: the preference to add")
    explanation: str = ""


class CompressionSummary(BaseModel):
    """One summary of several related memories."""

    summary: str = Field(description="One concise summary of all the entries")
    semantic_key: str = Field(description="A short phrase for vector search")


class PatternStep(BaseModel):
    description: str


class RoutineCandidate(BaseModel):
    """A repeated behaviour that could become a routine."""

    name: str = Field(description='Short snake_case identifier, e.g. "evening_dim"')
    trigger_pattern: str = Field(
        "", description='When it happens, e.g. "20:00 daily" or "weekday morning"'
    )
    steps: list[PatternStep] = Field(default_factory=list)
    confidence: float = Field(0.0, description="0.0 to 1.0")
    learned_from: list[str] = Field(
        default_factory=list, description="Episodic entry ids, from the [id] prefix"
    )


@dataclass(frozen=True)
class LibrarianTask:
    """One call's rendered prompt and, for analysis, how many elements it must return."""

    instructions: RenderedPrompt
    expected_items: int | None = None


@dataclass(frozen=True)
class LibrarianAgents:
    """The five agents, the model they run on, each request's timeout and the hook run
    after every ask (the spend alert)."""

    model: Model
    analyse: Agent[LibrarianTask, list[ObservationAnalysis]]
    extract: Agent[LibrarianTask, list[PreferenceUpdate]]
    resolve: Agent[LibrarianTask, list[ConflictItem]]
    compress: Agent[LibrarianTask, CompressionSummary]
    patterns: Agent[LibrarianTask, list[RoutineCandidate]]
    timeout_s: float = DEFAULT_SYSTEM2_TIMEOUT_S
    after_ask: Callable[[], Awaitable[object]] | None = None

    async def ask[T](
        self,
        agent: Agent[LibrarianTask, T],
        prompt: str,
        task: LibrarianTask,
        *,
        max_tokens: int,
    ) -> T:
        """Run *agent* once on this model, then the after-ask hook, even when it fails.

        A failed hook is logged, never raised: raising would discard a paid result, or
        replace the run's own exception with the bookkeeping one.
        """
        try:
            with linked_prompts(task.instructions.ref):
                result = await agent.run(
                    prompt,
                    deps=task,
                    model=self.model,
                    model_settings=ModelSettings(max_tokens=max_tokens, timeout=self.timeout_s),
                )
        finally:
            await self._after_ask()
        return result.output

    async def _after_ask(self) -> None:
        if self.after_ask is None:
            return
        try:
            await self.after_ask()
        except Exception:
            logger.warning("Librarian after-ask hook failed", exc_info=True)


def _output[T](model: Model, kind: type[T]) -> OutputSpec[T]:
    """JSON-schema output where the model supports it, an output tool where it does not."""
    if model.profile.get("supports_json_schema_output", False):
        return NativeOutput(kind)
    return kind


def _agent[T](
    model: Model, name: str, kind: type[T], spend: SpendLimits[Any] | None
) -> Agent[LibrarianTask, T]:
    capabilities: list[AbstractCapability[LibrarianTask]] = [instrumentation()]
    if spend is not None:
        capabilities.append(spend)
    agent: Agent[LibrarianTask, T] = Agent(
        None,
        output_type=_output(model, kind),
        deps_type=LibrarianTask,
        name=name,
        retries=OUTPUT_RETRIES,
        model_settings=ModelSettings(extra_headers=role_headers(LIBRARIAN)),
        capabilities=capabilities,
        defer_model_check=True,
    )

    @agent.instructions
    def _instructions(ctx: RunContext[LibrarianTask]) -> str:
        return ctx.deps.instructions.text

    return agent


def build_librarian_agents(
    model: Model,
    *,
    timeout_s: float = DEFAULT_SYSTEM2_TIMEOUT_S,
    spend: SpendLimits[Any] | None = None,
    after_ask: Callable[[], Awaitable[object]] | None = None,
) -> LibrarianAgents:
    """The Librarian's agents on *model*, built once per process. *timeout_s* bounds each
    request, so a hung provider cannot stall a nightly step: System 2's setting. *spend*
    is the process's daily budget, carried by every agent; *after_ask* runs after each
    ask."""
    analyse = _agent(model, "librarian-analyse", list[ObservationAnalysis], spend)

    @analyse.output_validator
    def _one_per_observation(
        ctx: RunContext[LibrarianTask], out: list[ObservationAnalysis]
    ) -> list[ObservationAnalysis]:
        expected = ctx.deps.expected_items
        if expected is not None and len(out) != expected:
            raise ModelRetry(
                f"Return exactly {expected} elements, one per numbered observation, in "
                f"order; you returned {len(out)}."
            )
        return out

    return LibrarianAgents(
        model=model,
        analyse=analyse,
        extract=_agent(model, "librarian-extract", list[PreferenceUpdate], spend),
        resolve=_agent(model, "librarian-resolve", list[ConflictItem], spend),
        compress=_agent(model, "librarian-compress", CompressionSummary, spend),
        patterns=_agent(model, "librarian-patterns", list[RoutineCandidate], spend),
        timeout_s=timeout_s,
        after_ask=after_ask,
    )
