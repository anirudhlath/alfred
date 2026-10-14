"""The Librarian's LLM calls: five small agents, each with a typed output.

Built once per process from ``LIBRARIAN_MODEL`` (``build_librarian_agents``) and run
per consolidation pass through ``LibrarianAgents.ask``, which caps the response,
carries the Librarian role header and records the tokens as spend.

The instructions say what to return. How to shape it is the output schema's job: it
travels with every request, as JSON-schema output where the model supports it and as an
output tool where it does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from shared.agents import instrumentation, model_key, role_headers
from shared.llm_roles import LIBRARIAN

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.output import OutputSpec

    from core.conscious.cost import CostTracker

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


ANALYSE_INSTRUCTIONS: Final = (
    "You are a memory analyst for a home automation assistant. Analyse each numbered "
    "observation and return one element per observation, in the same order. For each, "
    "list the entity ids, room names or people it mentions, score its significance, and "
    "write a short human-readable semantic key summarising its core meaning (used for "
    "vector search).\n"
    "Significance runs from 0.0 to 1.0 per dimension: safety=1.0 for emergencies/alarms, "
    "novelty=1.0 for first-time events, personal=1.0 for direct user interactions, "
    "emotional=1.0 for strong sentiment."
)

EXTRACT_INSTRUCTIONS: Final = (
    "Analyze these home assistant observations. Return each clear user preference you "
    "detect (e.g., preferred temperature, routine change, dietary preference) as a domain "
    "and an observation. Only return high-confidence observations. If nothing is notable, "
    "return an empty list."
)

COMPRESS_INSTRUCTIONS: Final = (
    "You are a memory compressor for a home automation assistant. Given multiple related "
    "episodic memory entries, produce a single concise summary and a short semantic key "
    "phrase."
)


def resolve_instructions(min_observations: int, min_days: int) -> str:
    """Conflict resolution, with the evidence a contradiction needs."""
    return (
        "You are a memory conflict resolver for a home automation assistant. Given "
        "EXISTING learned preferences and NEW observations, classify each new observation "
        "against the existing preferences:\n"
        "- confirm: it agrees with an existing preference; give that preference's text as "
        "line.\n"
        "- contradict: it revises one; give the existing text as old and the revised text "
        "as new.\n"
        "- new: it adds a preference; give it as content.\n"
        "Explain each decision in explanation.\n\n"
        f"IMPORTANT: Only allow 'contradict' if supported by at least {min_observations} "
        f"consistent observations over at least {min_days} days. Otherwise use 'confirm' "
        "or 'new'."
    )


def pattern_instructions(min_occurrences: int, min_days: int, tz_name: str) -> str:
    """Pattern detection, in the user's zone, with the Reflex paragraph."""
    return (
        "You are a pattern analyst for a home automation assistant. "
        "Given episodic memory entries, identify repeated behavioural patterns. "
        f"Only report patterns with at least {min_occurrences} occurrences "
        f"spread over at least {min_days} different days. "
        f"Entry times are local ({tz_name}); give trigger_pattern times in that "
        "same local time."
        "\n\n"
        "PAY SPECIAL ATTENTION to entries with source 'reflex' — these are automatic "
        "System 1 (Reflex Engine) actions taken without conscious reasoning. Look for:\n"
        "- Repeated reflex actions that may be unnecessary or counterproductive "
        "(e.g., lights turning on for pet motion at night)\n"
        "- Reflex patterns that could be optimised or overridden by a learned routine\n"
        "- Reflex actions that are consistent and beneficial (validate as good behaviour)\n\n"
        "Return each qualifying pattern with the episodic entry ids it was learned from. "
        "If no patterns qualify, return an empty list."
    )


@dataclass(frozen=True)
class LibrarianTask:
    """One call's instructions and, for analysis, how many elements it must return."""

    instructions: str
    expected_items: int | None = None


@dataclass(frozen=True)
class LibrarianAgents:
    """The five agents and the model they run on."""

    model: Model
    analyse: Agent[LibrarianTask, list[ObservationAnalysis]]
    extract: Agent[LibrarianTask, list[PreferenceUpdate]]
    resolve: Agent[LibrarianTask, list[ConflictItem]]
    compress: Agent[LibrarianTask, CompressionSummary]
    patterns: Agent[LibrarianTask, list[RoutineCandidate]]

    async def ask[T](
        self,
        agent: Agent[LibrarianTask, T],
        prompt: str,
        task: LibrarianTask,
        *,
        max_tokens: int,
        cost: CostTracker | None,
    ) -> T:
        """Run *agent* once on this model; its tokens are recorded even when it fails."""
        usage = RunUsage()
        try:
            result = await agent.run(
                prompt,
                deps=task,
                model=self.model,
                model_settings=ModelSettings(max_tokens=max_tokens),
                usage=usage,
            )
        finally:
            if cost is not None:
                await cost.record_spend(
                    usage.input_tokens,
                    usage.output_tokens,
                    model_key(self.model),
                    count_request=False,
                )
        return result.output


def _output[T](model: Model, kind: type[T]) -> OutputSpec[T]:
    """JSON-schema output where the model supports it, an output tool where it does not."""
    if model.profile.get("supports_json_schema_output", False):
        return NativeOutput(kind)
    return kind


def _agent[T](model: Model, name: str, kind: type[T]) -> Agent[LibrarianTask, T]:
    agent: Agent[LibrarianTask, T] = Agent(
        None,
        output_type=_output(model, kind),
        deps_type=LibrarianTask,
        name=name,
        retries=OUTPUT_RETRIES,
        model_settings=ModelSettings(extra_headers=role_headers(LIBRARIAN)),
        capabilities=[instrumentation()],
        defer_model_check=True,
    )

    @agent.instructions
    def _instructions(ctx: RunContext[LibrarianTask]) -> str:
        return ctx.deps.instructions

    return agent


def build_librarian_agents(model: Model) -> LibrarianAgents:
    """The Librarian's agents on *model*, built once per process."""
    analyse = _agent(model, "librarian-analyse", list[ObservationAnalysis])

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
        extract=_agent(model, "librarian-extract", list[PreferenceUpdate]),
        resolve=_agent(model, "librarian-resolve", list[ConflictItem]),
        compress=_agent(model, "librarian-compress", CompressionSummary),
        patterns=_agent(model, "librarian-patterns", list[RoutineCandidate]),
    )
