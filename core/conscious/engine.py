"""Conscious Engine — System 2, the conversational brain.

A request is answered in one agent run (``core.conscious.agent``): the engine resolves
identity, checks the budget, gathers the turn's context into ``TurnDeps`` and runs the
agent over the session's whole message history, then stores the run's messages back
(``core.conscious.session``). The agent's ``SpendLimits`` counts every model response,
including those of a run that fails, and refuses a request once the day's budget is gone;
the turn then gets the budget reply, after what it already did if it acted. A model that
runs out of requests or retries gets the deliberating reply, because replaying the request
would spend again and repeat its tool calls; a provider or transport failure raises, so
the request stays pending and is retried.

A run whose critical calls wait for the owner ends in ``DeferredToolRequests``: the engine
parks it (``core.conscious.approvals.park``), which asks the owner, stores the session with
the approval it waits on, and answers with a fixed template.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from pydantic_ai import DeferredToolRequests
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.usage import RunUsage, UsageLimits
from pydantic_ai_harness.spend import SpendLimitExceeded

from bus.schemas.events import AlfredResponse, ParkedRun, UserRequest
from core.conscious.agent import REQUEST_LIMIT
from core.conscious.approvals import (
    approval_id_for,
    confirmation_reply,
    park,
    parked_calls,
    summary_of,
)
from core.conscious.deps import TurnDeps, TurnServices
from core.conscious.effects import EffectLedger, ledger_reply
from core.conscious.session import text_turn
from core.conscious.system2_prompts import fetch_system2_prompts
from core.conscious.tools.integrations import fetch_integration_tools
from shared.config import DEFAULT_INVOLUNTARY_RECALL_THRESHOLD
from shared.otel import langfuse_attributes
from shared.prompts.link import linked_prompts
from shared.prompts.store import PromptStore
from shared.streams import SCRATCHPAD_QUEUE
from shared.traced import traced
from shared.usertime import (
    get_user_timezone,
    is_valid_timezone,
    set_user_timezone,
)

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai_harness.spend import SpendLimits

    from bus.schemas.events import ChannelName, ContentType
    from core.conscious.approvals import Notifier
    from core.conscious.identity import IdentityGate
    from core.conscious.session import SessionManager
    from core.conscious.tools.integrations import IntegrationTool
    from core.identity.schemas import IdentityResult
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
# Follows the ledger's reply when the budget ran out after the turn acted.
BUDGET_AFTER_ACTING: Final = (
    "We've reached the daily budget, so I'll need to defer complex requests until tomorrow."
)
DELIBERATING_REPLY: Final = (
    "I apologize, sir — I've been deliberating too long. Let me try a more direct approach."
)
NOT_CONFIGURED_REPLY: Final = (
    "I'm afraid my connection to the thinking engine is not configured, sir."
)

Mood = Literal["neutral", "concerned"]


@dataclass
class ConsciousConfig:
    """Involuntary recall's settings; populate from ``AlfredConfig`` at the call site."""

    involuntary_recall_limit: int = 10
    involuntary_recall_threshold: float = DEFAULT_INVOLUNTARY_RECALL_THRESHOLD


@dataclass
class ConsciousDeps:
    """The engine's services, its agent and the model the agent runs on.

    ``model`` is None when ``SYSTEM2_MODEL`` resolved to nothing at startup; every request
    then gets the not-configured reply. ``spend`` is the daily budget the agent carries as
    a capability; the engine reads it before a turn starts.
    ``notifier`` asks the owner to approve a parked run.
    ``prompts`` serves the turn's prose; the default serves the repo defaults.
    """

    redis: AioRedis
    identity_gate: IdentityGate
    session_mgr: SessionManager
    spend: SpendLimits[Any]
    domain_router: DomainRouter
    tool_registry: ToolRegistry
    context_reader: ContextReader
    agent: Agent[TurnDeps, str | DeferredToolRequests]
    model: Model | None
    notifier: Notifier
    routine_store: RoutineStore | None = None
    context_index: ContextIndexManager | None = None
    config: ConsciousConfig = field(default_factory=ConsciousConfig)
    prompts: PromptStore = field(default_factory=PromptStore)


class ConsciousEngine:
    """The conversational brain of Alfred (System 2)."""

    def __init__(self, *, deps: ConsciousDeps) -> None:
        self._redis = deps.redis
        self._identity_gate = deps.identity_gate
        self._session_mgr = deps.session_mgr
        self._spend = deps.spend
        self._tool_registry = deps.tool_registry
        self._agent = deps.agent
        self._model = deps.model
        self._notifier = deps.notifier
        self._routines = deps.routine_store
        self._context_index = deps.context_index
        self._prompts = deps.prompts
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
    async def process_request(
        self, request: UserRequest, *, received_ms: int | None = None
    ) -> AlfredResponse:
        """Answer one request: identity, budget, the turn's context, one agent run.

        *received_ms* is when the request entered the requests stream, its entry id's
        milliseconds; the runner passes it, so a side-effecting call made past the replay
        window is refused (``RecordEffects``). A direct caller omits it: the turn starts now.
        """
        now = datetime.now(UTC)
        tz_name = await self._timezone(request)

        identity = self._resolve(request)
        logger.info(
            "Identity resolved: %s (method=%s, confidence=%.2f)",
            identity.identity,
            identity.method,
            identity.confidence,
        )

        # The clock that picks the budget day reads the zone _timezone just resolved.
        if await self._spend.exhausted():
            logger.warning("Daily budget exceeded — returning System 1 fallback")
            return self._reply(request, BUDGET_REPLY, [], mood="concerned")

        session = await self._session_mgr.load(
            request.session_id, request.channel, identity.identity
        )
        integration_tools = await self._integration_tools(identity.identity)
        prompts = await fetch_system2_prompts(
            self._prompts,
            who=identity.identity,
            channel=request.channel,
            content_type=request.content_type,
            area=request.area,
            integrations_available=bool(integration_tools),
        )
        deps = TurnDeps(
            identity=identity,
            session_id=request.session_id,
            channel=request.channel,
            request_id=request.event_id,
            tz_name=tz_name,
            now=now,
            services=self._services,
            received_ms=received_ms if received_ms is not None else int(now.timestamp() * 1000),
            content_type=request.content_type,
            area=request.area,
            recall=await self._recall(request.content),
            routine_hint=self._routine_hint(now, tz_name),
            registry_tools=await self._tool_registry.get_tools(),
            prompts=prompts,
            integration_tools=integration_tools,
        )

        usage = RunUsage()
        text, mood, messages, deferred = await self._run(request, deps, session.messages, usage)
        pending: str | None = None
        if deferred is not None:
            text, messages, pending = await self._park(
                deps,
                deferred,
                messages,
                channel=request.channel,
                content_type=request.content_type,
                request_event_id=request.event_id,
            )

        # Stored before the reply is published, as before. A crash between the two stores
        # the turn, and a redelivery then runs again over a history that already holds it
        # (or appends a ledger text turn): accepted, the same order as the text-only sessions.
        await self._session_mgr.store_run(
            request.session_id, messages, identity.identity, pending_approval=pending
        )
        await self._note(now, request.content, text, deps.actions_taken, usage)
        return self._reply(request, text, deps.actions_taken, mood=mood)

    async def _park(
        self,
        deps: TurnDeps,
        deferred: DeferredToolRequests,
        messages: list[ModelMessage],
        *,
        channel: ChannelName,
        content_type: ContentType,
        request_event_id: str,
    ) -> tuple[str, list[ModelMessage], str]:
        """Park a run that deferred its critical calls, under one approval derived from the
        turn's request id, and ask the owner. Returns the turn's reply, the history to store
        and the approval id.

        All three come from the run ``park`` returns, not the one built here: on a redelivery
        that is the stored record, which the owner is already looking at, and its calls may
        differ from the ones this run's model deferred.
        """
        calls = parked_calls(deferred)
        run = ParkedRun(
            approval_id=approval_id_for(deps.request_id),
            session_id=deps.session_id,
            channel=channel,
            identity=deps.identity.model_dump(mode="json"),
            request_event_id=request_event_id,
            created_at=deps.now,
            content_type=content_type,
            area=deps.area,
            tz_name=deps.tz_name,
            summary=summary_of(calls),
            calls=calls,
            messages=ModelMessagesTypeAdapter.dump_json(messages).decode(),
        )
        parked = await park(self._redis, self._notifier, run, ledger_key=deps.request_id)
        logger.info(
            "Parked %d call(s) for approval %s: %s",
            len(parked.calls),
            parked.approval_id,
            ", ".join(call.tool_name for call in parked.calls),
        )
        history = ModelMessagesTypeAdapter.validate_json(parked.messages)
        return confirmation_reply(parked.summary), history, parked.approval_id

    def _resolve(self, request: UserRequest) -> IdentityResult:
        """Who sent *request*, by the identity gate."""
        return self._identity_gate.resolve(
            channel=request.channel,
            identity_claim=request.identity_claim,
            authenticated=request.authenticated,
            identity_confidence=request.identity_confidence,
        )

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
        history: list[ModelMessage],
        usage: RunUsage,
    ) -> tuple[str, Mood, list[ModelMessage], DeferredToolRequests | None]:
        """One agent run: its reply, its mood, the session's history after it, and the calls
        it deferred for approval, if it ended waiting on them (its reply is then empty).

        A finished run's history is ``all_messages()``, compacted during the run. A run
        that ends without its own answer leaves *history* plus the turn as text, never its
        half-finished messages. *usage* accumulates across it, for the note.
        """
        model = self._model
        if model is None:
            return self._ended(request, history, NOT_CONFIGURED_REPLY, "neutral")
        try:
            with (
                langfuse_attributes(
                    session_id=request.session_id, user_id=deps.who, tags=[request.channel]
                ),
                linked_prompts(*deps.prompts.refs()),
            ):
                result = await self._agent.run(
                    request.content,
                    deps=deps,
                    message_history=history,
                    model=model,
                    usage=usage,
                    usage_limits=UsageLimits(request_limit=REQUEST_LIMIT),
                )
            if isinstance(result.output, DeferredToolRequests):
                return "", "neutral", result.all_messages(), result.output
            return result.output, "neutral", result.all_messages(), None
        except SpendLimitExceeded:
            # A UsageLimitExceeded too, so it is caught first. Raised before a request,
            # once an earlier response in this run (or another process) spent the day.
            logger.warning("Daily budget spent during the run — returning the budget reply")
            return self._ended(request, history, await self._budget_reply(request), "concerned")
        except UsageLimitExceeded:
            logger.warning("System 2 used its %d model requests without answering", REQUEST_LIMIT)
            return self._ended(request, history, DELIBERATING_REPLY, "neutral")
        except UnexpectedModelBehavior as exc:
            # Retries exhausted, blank replies, or the token limit hit before any output.
            # ModelAPIError (HTTP, connection, timeout) is a sibling, not a subclass, so a
            # provider failure still raises and the request is retried.
            logger.warning("System 2 gave up on the model's replies: %s", exc)
            return self._ended(request, history, DELIBERATING_REPLY, "neutral")

    @staticmethod
    def _ended(
        request: UserRequest, history: list[ModelMessage], text: str, mood: Mood
    ) -> tuple[str, Mood, list[ModelMessage], None]:
        """A turn answered with *text* instead of the run's own answer."""
        return text, mood, [*history, *text_turn(request.content, text)], None

    async def _budget_reply(self, request: UserRequest) -> str:
        """The budget reply for a run the budget stopped partway. If the turn already
        acted, say what it did first (``ledger_reply``), so a light that went on is never
        answered as though nothing happened; an unreadable ledger gets the plain reply."""
        try:
            effects = await EffectLedger(self._redis).read(request.event_id)
        except Exception:
            logger.warning("Could not read the ledger for the budget reply", exc_info=True)
            return BUDGET_REPLY
        if not effects:
            return BUDGET_REPLY
        return f"{ledger_reply(effects)} {BUDGET_AFTER_ACTING}"

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

    async def record_partial_turn(self, request: UserRequest, text: str) -> None:
        """Store a turn answered from its side-effect ledger (*text*) in the session, as
        text after the stored history, so the next turn knows what was done. Called once
        the answer is published; a failed write is logged, not raised."""
        try:
            who = self._resolve(request).identity
            await self._session_mgr.append_text_turn(request.session_id, who, request.content, text)
        except Exception:
            logger.warning("Failed to store a partial turn in the session", exc_info=True)

    @staticmethod
    def failure_reply(request: UserRequest, text: str) -> AlfredResponse:
        """The answer to a turn that could not finish: a normal reply's channel, session,
        source and ``in_reply_to``, no actions, concerned."""
        return ConsciousEngine._reply(request, text, [], mood="concerned")

    @staticmethod
    def _reply(
        request: UserRequest,
        text: str,
        actions: list[str],
        *,
        mood: Mood = "neutral",
    ) -> AlfredResponse:
        return AlfredResponse(
            source=SOURCE,
            channel=request.channel,
            session_id=request.session_id,
            text=text,
            actions_taken=actions,
            mood=mood,
            in_reply_to=request.event_id,
        )
