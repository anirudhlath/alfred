"""Conscious Engine — System 2, the conversational brain.

A request is answered in one agent run (``core.conscious.agent``): the engine resolves
identity, checks the budget, gathers the turn's context into ``TurnDeps`` and runs the
agent over the session's history. Spend is recorded for every run that starts, including
one that fails. A model that runs out of requests or retries gets the deliberating reply,
because replaying the request would spend again and repeat its tool calls; a provider or
transport failure raises, so the request stays pending and is retried.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage, UsageLimits

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.agent import REQUEST_LIMIT
from core.conscious.deps import TurnDeps, TurnServices
from core.conscious.tools.integrations import fetch_integration_tools
from shared.agents import model_key
from shared.config import DEFAULT_INVOLUNTARY_RECALL_THRESHOLD
from shared.otel import langfuse_attributes
from shared.streams import SCRATCHPAD_QUEUE
from shared.traced import traced
from shared.usertime import (
    get_user_timezone,
    is_valid_timezone,
    set_user_timezone,
)

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.models import Model

    from core.conscious.cost import CostTracker
    from core.conscious.identity import IdentityGate
    from core.conscious.session import SessionManager
    from core.conscious.tools.integrations import IntegrationTool
    from core.memory.context_index import ContextIndexManager
    from core.memory.routines.store import RoutineStore
    from core.memory.schemas import RoutineSpec
    from core.memory.vector_store import SearchResult
    from core.reflex.context_reader import ContextReader
    from core.reflex.tool_registry import ToolRegistry
    from core.routing.domain_router import DomainRouter
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

SOURCE: Final = "conscious-engine"
BUDGET_REPLY: Final = (
    "I'm afraid we've reached the daily budget, sir. "
    "I'm operating in reduced capacity — ambient actions continue, "
    "but I'll need to defer complex requests until tomorrow."
)
DELIBERATING_REPLY: Final = (
    "I apologize, sir — I've been deliberating too long. Let me try a more direct approach."
)
NOT_CONFIGURED_REPLY: Final = (
    "I'm afraid my connection to the thinking engine is not configured, sir."
)


def session_messages(history: list[dict[str, str]]) -> list[ModelMessage]:
    """The stored session as the run's message history.

    Sessions store only what was said, user and assistant text; tool calls and results are
    not kept between turns yet. Anything else in the list is skipped.
    """
    messages: list[ModelMessage] = []
    for turn in history:
        content = turn.get("content", "")
        if turn.get("role") == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content)]))
        elif turn.get("role") == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content)]))
    return messages


@dataclass
class ConsciousConfig:
    """Involuntary recall's settings; populate from ``AlfredConfig`` at the call site."""

    involuntary_recall_limit: int = 10
    involuntary_recall_threshold: float = DEFAULT_INVOLUNTARY_RECALL_THRESHOLD


@dataclass
class ConsciousDeps:
    """The engine's services, its agent and the model the agent runs on.

    ``model`` is None when ``SYSTEM2_MODEL`` resolved to nothing at startup; every request
    then gets the not-configured reply.
    """

    redis: AioRedis
    identity_gate: IdentityGate
    session_mgr: SessionManager
    cost_tracker: CostTracker
    domain_router: DomainRouter
    tool_registry: ToolRegistry
    context_reader: ContextReader
    agent: Agent[TurnDeps, str]
    model: Model | None
    routine_store: RoutineStore | None = None
    context_index: ContextIndexManager | None = None
    config: ConsciousConfig = field(default_factory=ConsciousConfig)


class ConsciousEngine:
    """The conversational brain of Alfred (System 2)."""

    def __init__(self, *, deps: ConsciousDeps) -> None:
        self._redis = deps.redis
        self._identity_gate = deps.identity_gate
        self._session_mgr = deps.session_mgr
        self._cost = deps.cost_tracker
        self._tool_registry = deps.tool_registry
        self._agent = deps.agent
        self._model = deps.model
        self._routines = deps.routine_store
        self._context_index = deps.context_index
        self._recall_limit = deps.config.involuntary_recall_limit
        self._recall_threshold = deps.config.involuntary_recall_threshold
        self._services = TurnServices(
            redis=deps.redis,
            router=deps.domain_router,
            context_reader=deps.context_reader,
            context_index=deps.context_index,
        )

    @property
    def has_routine_store(self) -> bool:
        """Whether a routine store is configured."""
        return self._routines is not None

    _ROUTINE_SUGGESTION_COOLDOWN_HOURS: ClassVar[int] = 24

    def _eligible_candidates(self, now: datetime, tz_name: str) -> list[RoutineSpec]:
        """Return candidate routines that match current time and are outside cooldown."""
        if self._routines is None:
            return []

        from core.memory.routines.patterns import match_trigger_pattern

        eligible: list[RoutineSpec] = []
        for routine in self._routines.list_by_state("candidate"):
            if routine.last_suggested is not None:
                hours_since = (now - routine.last_suggested).total_seconds() / 3600
                if hours_since < self._ROUTINE_SUGGESTION_COOLDOWN_HOURS:
                    continue
            if not match_trigger_pattern(routine.trigger_pattern, now, tz_name):
                continue
            eligible.append(routine)
        return eligible

    def _build_routine_hint(self, now: datetime, tz_name: str) -> str:
        """Check candidate routines for time-pattern matches and return a hint string.

        Returns an empty string when no routines match or ``_routines`` is None.
        Routines that were suggested within the cooldown window are skipped.
        Matching routines have their ``last_suggested`` timestamp updated.
        """
        if self._routines is None:
            return ""

        eligible = self._eligible_candidates(now, tz_name)
        hints: list[str] = []

        for routine in eligible:
            updated = routine.model_copy(update={"last_suggested": now})
            self._routines.save(updated)

            steps_str = "; ".join(s.description for s in routine.steps) if routine.steps else "N/A"
            hints.append(
                f"[routine-suggestion] You've noticed a pattern: {routine.name} "
                f"({routine.trigger_pattern}). Steps: {steps_str}. "
                f"Confidence: {routine.confidence:.0%}. "
                f"If appropriate, suggest this to sir and ask if they'd like "
                f"Alfred to handle this automatically."
            )
            logger.debug("Routine suggestion injected: '%s'", routine.name)

        return "\n\n".join(hints)

    async def check_routine_suggestions(
        self,
        now: datetime | None = None,
        notifier: Any = None,
    ) -> None:
        """Check candidate routines and publish proactive notifications for matches.

        Called periodically from the conscious process background loop.
        Routines that match the current time pattern and are outside the suggestion
        cooldown window receive an INFORMATIONAL notification push.
        """
        if self._routines is None or notifier is None:
            return

        from core.notifications.schema import Urgency

        now = now or datetime.now(UTC)
        tz_name = await get_user_timezone(self._redis)

        for routine in self._eligible_candidates(now, tz_name):
            steps_str = "; ".join(s.description for s in routine.steps) if routine.steps else ""
            if steps_str:
                body = (
                    f"I've noticed a pattern: '{routine.name}' — {steps_str} "
                    f"around {routine.trigger_pattern}. "
                    f"Want me to start doing this automatically?"
                )
            else:
                body = (
                    f"I've noticed a recurring pattern: '{routine.name}' "
                    f"around {routine.trigger_pattern}. "
                    f"Want me to start doing this automatically?"
                )

            # Update last_suggested BEFORE publishing to avoid spam if publish raises
            updated = routine.model_copy(update={"last_suggested": now})
            self._routines.save(updated)

            await notifier.publish(
                title="Routine Suggestion",
                body=body,
                source="librarian",
                urgency=Urgency.INFORMATIONAL,
            )
            logger.info("Proactive routine suggestion published: '%s'", routine.name)

    # No @track_latency (#308): its buffer was never flushed, and the agent run span
    # measures the same latency.
    @traced(name="conscious.process_request")
    async def process_request(self, request: UserRequest) -> AlfredResponse:
        """Answer one request: identity, budget, the turn's context, one agent run."""
        now = datetime.now(UTC)
        tz_name = await self._timezone(request)

        identity = self._identity_gate.resolve(
            channel=request.channel,
            identity_claim=request.identity_claim,
            authenticated=request.authenticated,
            identity_confidence=request.identity_confidence,
        )
        logger.info(
            "Identity resolved: %s (method=%s, confidence=%.2f)",
            identity.identity,
            identity.method,
            identity.confidence,
        )

        if await self._cost.is_budget_exceeded():
            logger.warning("Daily budget exceeded — returning System 1 fallback")
            return self._reply(request, BUDGET_REPLY, [], mood="concerned")

        session = await self._session_mgr.get_or_create(request.session_id, request.channel)
        deps = TurnDeps(
            identity=identity,
            session_id=request.session_id,
            channel=request.channel,
            request_id=request.event_id,
            tz_name=tz_name,
            now=now,
            services=self._services,
            content_type=request.content_type,
            area=request.area,
            recall=await self._recall(request.content),
            routine_hint=self._routine_hint(now, tz_name),
            registry_tools=await self._tool_registry.get_tools(),
            integration_tools=await self._integration_tools(identity.identity),
        )

        usage = RunUsage()
        text = await self._run(request, deps, session["history"], usage)

        await self._session_mgr.append_turn(request.session_id, "user", request.content)
        await self._session_mgr.append_turn(request.session_id, "assistant", text)
        await self._note(now, request.content, text, deps.actions_taken, usage)
        return self._reply(request, text, deps.actions_taken)

    async def _timezone(self, request: UserRequest) -> str:
        """The client's zone when it sent a valid one, persisted; else the stored zone."""
        if request.timezone and is_valid_timezone(request.timezone):
            # Persisted at the domain boundary, before any tool (run_at normalisation,
            # cron) reads the stored key. Write-on-change: one cheap GET per request.
            await set_user_timezone(self._redis, request.timezone)
            return request.timezone
        return await get_user_timezone(self._redis)

    async def _recall(self, content: str) -> list[SearchResult]:
        """Involuntary recall: memories close enough to the request to show unasked."""
        if self._context_index is None or not content:
            return []
        try:
            return await self._context_index.search_text(
                content, limit=self._recall_limit, min_similarity=self._recall_threshold
            )
        except Exception:
            logger.warning("Involuntary recall failed", exc_info=True)
            return []

    def _routine_hint(self, now: datetime, tz_name: str) -> str:
        if self._routines is None:
            return ""
        try:
            return self._build_routine_hint(now, tz_name)
        except Exception:
            logger.warning("Routine suggestion check failed", exc_info=True)
            return ""

    async def _integration_tools(self, who: str) -> list[IntegrationTool]:
        """The integrations' capabilities, read once per turn; sir only."""
        if who != "sir":
            return []
        try:
            return await fetch_integration_tools()
        except Exception:
            logger.warning("Failed to list integration tools", exc_info=True)
            return []

    async def _run(
        self,
        request: UserRequest,
        deps: TurnDeps,
        history: list[dict[str, str]],
        usage: RunUsage,
    ) -> str:
        """One agent run. *usage* accumulates across it and is what spend is recorded from."""
        model = self._model
        if model is None:
            return NOT_CONFIGURED_REPLY
        try:
            with langfuse_attributes(
                session_id=request.session_id, user_id=deps.who, tags=[request.channel]
            ):
                result = await self._agent.run(
                    request.content,
                    deps=deps,
                    message_history=session_messages(history),
                    model=model,
                    usage=usage,
                    usage_limits=UsageLimits(request_limit=REQUEST_LIMIT),
                )
            return result.output
        except UsageLimitExceeded:
            logger.warning("System 2 used its %d model requests without answering", REQUEST_LIMIT)
            return DELIBERATING_REPLY
        except UnexpectedModelBehavior as exc:
            # Retries exhausted, blank replies, or the token limit hit before any output.
            # ModelAPIError (HTTP, connection, timeout) is a sibling, not a subclass, so a
            # provider failure still raises and the request is retried.
            logger.warning("System 2 gave up on the model's replies: %s", exc)
            return DELIBERATING_REPLY
        finally:
            await self._record_spend(usage, model)

    async def _record_spend(self, usage: RunUsage, model: Model) -> None:
        """Spend for one run. A failed write is logged, never raised: raising here would
        replace the run's reply and replay the request, repeating its tool calls."""
        try:
            await self._cost.record_spend(usage.input_tokens, usage.output_tokens, model_key(model))
        except Exception:
            logger.warning("Failed to record System 2 spend", exc_info=True)

    async def _note(
        self, now: datetime, content: str, text: str, actions: list[str], usage: RunUsage
    ) -> None:
        """One line on the scratchpad for the Librarian: the question, never the reply."""
        try:
            timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            actions_str = ", ".join(actions) if actions else "none"
            observation = (
                f"{timestamp} [conscious] "
                f"user='{content[:80]}' → {len(text)} chars "
                f"(actions={actions_str}, tokens={usage.input_tokens}+{usage.output_tokens})"
            )
            await self._redis.lpush(SCRATCHPAD_QUEUE, observation)
        except Exception as exc:
            logger.warning("Failed to write scratchpad observation: %s", exc)

    @staticmethod
    def _reply(
        request: UserRequest,
        text: str,
        actions: list[str],
        *,
        mood: Literal["neutral", "concerned"] = "neutral",
    ) -> AlfredResponse:
        return AlfredResponse(
            source=SOURCE,
            channel=request.channel,
            session_id=request.session_id,
            text=text,
            actions_taken=actions,
            mood=mood,
        )
