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
the approval it waits on, and answers with a fixed template. The owner's next turn in that
session is read as approving, declining or moving on from it, and resumes the parked run
with the verdict and the turn's words in one run. A decision from the Door resumes it as a
turn of its own (``resume_approval``), under the approval id, answered as a follow-up.

A reply that no parked run in its conversation takes may still answer the ask Reflex has open
(``_answer_reflex_ask``): a yes or no is recorded and answered with a short fixed reply, and
no model runs, before the turn's recall and prompts are built. It does not once Alfred has
spoken in that conversation since shortly before the ask went out (``ASK_LOOKBACK_SECONDS``):
a "yes" then answers Alfred.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded, UserError
from pydantic_ai.messages import ModelMessagesTypeAdapter, repair_messages
from pydantic_ai.usage import RunUsage, UsageLimits
from pydantic_ai_harness.spend import SpendLimitExceeded

from bus.schemas.events import (
    AlfredResponse,
    ApprovalDecision,
    ParkedAction,
    ParkedCall,
    ParkedRun,
    UserRequest,
)
from core.conscious.agent import REQUEST_LIMIT
from core.conscious.approvals import (
    approval_id_for,
    claimable_run,
    confirmation_reply,
    park,
    parked_calls,
    results_for,
    summary_of,
)
from core.conscious.deps import TurnDeps, TurnServices
from core.conscious.effects import EffectLedger, ledger_reply
from core.conscious.session import spoke_since, text_turn
from core.conscious.system2_prompts import fetch_system2_prompts
from core.conscious.tools.integrations import fetch_integration_tools
from core.identity.schemas import IdentityResult
from core.reflex.answers import (
    ASK_LOOKBACK_SECONDS,
    ASK_REPLY_CHANNELS,
    ASK_SOURCE,
    read_open_ask,
    settle_expired,
)
from core.routing.pending import claim_pending, decide, read_pending
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
    from core.conscious.approval_classifier import ApprovalClassifier
    from core.conscious.approvals import Decision, Notifier
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
# The replies to a "yes" and a "no" that answered one of Reflex's asks.
ASK_CONFIRMED_REPLY: Final = "Okay."
ASK_DECLINED_REPLY: Final = "Okay, I'll leave it."
# The owner's side of a Door decision, as the session records it: the Door sends no words.
APPROVED_MARKER: Final = "[Approved from the confirmation prompt]"
DECLINED_MARKER: Final = "[Declined from the confirmation prompt]"

Mood = Literal["neutral", "concerned"]


@dataclass
class ConsciousConfig:
    """Involuntary recall's settings; populate from ``AlfredConfig`` at the call site."""

    involuntary_recall_limit: int = 10
    involuntary_recall_threshold: float = DEFAULT_INVOLUNTARY_RECALL_THRESHOLD


@dataclass(frozen=True)
class _Resume:
    """A parked run a reply decided: the history to continue and the verdict on its calls."""

    approval_id: str
    history: list[ModelMessage]
    results: DeferredToolResults


@dataclass
class ConsciousDeps:
    """The engine's services, its agent and the model the agent runs on.

    ``model`` is None when ``SYSTEM2_MODEL`` resolved to nothing at startup; every request
    then gets the not-configured reply. ``spend`` is the daily budget the agent carries as
    a capability; the engine reads it before a turn starts.
    ``notifier`` asks the owner to approve a parked run; ``approval`` reads the owner's reply
    to it, and with None no reply approves anything.
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
    approval: ApprovalClassifier | None = None
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
        self._approval = deps.approval
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
        # A yes or no to Reflex's ask needs neither recall nor prompts, so it is answered before
        # the turn's context is built, unless this session waits on a parked run: that run
        # takes the reply first (``_resume_for``, below) and the ask only what it leaves.
        ask_checked = session.pending_approval is None
        if ask_checked:
            ack = await self._answer_reflex_ask(request, identity.identity, session.messages)
            if ack is not None:  # no model run, nothing parked, nothing stored in the session
                return self._reply(request, ack, [])
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

        history, results = session.messages, None
        resume = await self._resume_for(request, identity.identity, session.pending_approval)
        if resume is not None:
            history, results = resume.history, resume.results
            deps.approval_id = resume.approval_id
        elif not ask_checked:
            ack = await self._answer_reflex_ask(request, identity.identity, session.messages)
            if ack is not None:
                return self._reply(request, ack, [])

        usage = RunUsage()
        text, mood, messages, deferred = await self._run(
            deps, history, usage, prompt=request.content, said=request.content, results=results
        )
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

    @traced(name="conscious.resume_approval")
    async def resume_approval(
        self, decision: ApprovalDecision, *, received_ms: int
    ) -> AlfredResponse:
        """Resume the run the owner approved or declined from the Door, as a turn of its own.

        The decision carries the whole parked run, already claimed by the Door. The run
        resumes as the record's identity on the record's session, under the approval id as
        its request id (its own ledger and answered marker), with the verdict on its
        deferred calls and no new prompt. *received_ms* is when the decision entered its
        stream, so the replay guard measures from the owner's tap. Only the calls the
        record holds are decided: a critical call the resumed run makes defers again and is
        parked as ``process_request`` parks one. The reply is a follow-up to the request
        that parked the run.

        Raises ``UserError`` when the run's history or identity no longer reads: the record
        is gone, so a retry could never resume it, and the runner treats the error as
        permanent (one ``FAILED_REPLY`` follow-up, then a dead letter holding the decision).
        """
        run = decision.run
        if await self._spend.exhausted():
            logger.warning("Daily budget exceeded — the Door's decision gets the budget reply")
            return self._followup(run, BUDGET_REPLY, [], mood="concerned")
        try:
            history = ModelMessagesTypeAdapter.validate_json(run.messages)
            identity = IdentityResult.model_validate(run.identity)
        except ValueError as exc:
            msg = f"Approval {decision.approval_id} holds a run that no longer reads"
            raise UserError(msg) from exc

        now = datetime.now(UTC)
        integration_tools = await self._integration_tools(identity.identity)
        prompts = await fetch_system2_prompts(
            self._prompts,
            who=identity.identity,
            channel=run.channel,
            content_type=run.content_type,
            area=run.area,
            integrations_available=bool(integration_tools),
        )
        deps = TurnDeps(
            identity=identity,
            session_id=run.session_id,
            channel=run.channel,
            request_id=decision.approval_id,
            tz_name=run.tz_name,
            now=now,
            services=self._services,
            received_ms=received_ms,
            content_type=run.content_type,
            area=run.area,
            registry_tools=await self._tool_registry.get_tools(),
            prompts=prompts,
            integration_tools=integration_tools,
            approval_id=decision.approval_id,
        )
        logger.info("Door decides approval %s: %s", decision.approval_id, decision.decision)

        marker = _marker(decision.decision)
        usage = RunUsage()
        text, mood, messages, deferred = await self._run(
            deps,
            history,
            usage,
            prompt=None,
            said=marker,
            results=results_for(run, decision.decision),
        )
        pending: str | None = None
        if deferred is not None:
            text, messages, pending = await self._park(
                deps,
                deferred,
                messages,
                channel=run.channel,
                content_type=run.content_type,
                request_event_id=run.request_event_id,
            )
        await self._store_resumed(decision, identity.identity, messages, text, pending)
        await self._note(now, marker, text, deps.actions_taken, usage)
        return self._followup(run, text, deps.actions_taken, mood=mood)

    async def _store_resumed(
        self,
        decision: ApprovalDecision,
        who: str,
        messages: list[ModelMessage],
        text: str,
        pending: str | None,
    ) -> None:
        """Store a Door resume (ruling P13). While the session still waits on this approval,
        the resumed run's messages replace its history and *pending* (a re-park's approval)
        is set. Otherwise a turn came in between: it closed the open call, replaced the
        history and cleared the approval, so the resume is added to the current history as
        text, and a re-park stays answerable from the Door alone."""
        run = decision.run
        session = await self._session_mgr.load(run.session_id, run.channel, who)
        if session.pending_approval == decision.approval_id:
            await self._session_mgr.store_run(
                run.session_id, messages, who, pending_approval=pending
            )
            return
        logger.info(
            "A turn came before approval %s was decided; stored as text", decision.approval_id
        )
        await self._session_mgr.append_text_turn(
            run.session_id, who, _marker(decision.decision), text
        )

    async def _resume_for(
        self, request: UserRequest, who: str, approval_id: str | None
    ) -> _Resume | None:
        """The parked run *request* decides, claimed, with the verdict its words give.

        Only the owner decides by reply, and only a run parked for them on this session
        (``claimable_run``). The record's history is read and the reply classified before
        the record is claimed (``GETDEL``): a slow classifier never holds a claimed record,
        and a history that no longer reads leaves the approval unclaimed. A crash after the
        claim loses the reply, never runs anything unapproved: the retry is a normal turn in
        which the open call reads as interrupted, and the owner is asked again only if the
        model proposes the call again. With no classifier every reply moves on. None when
        there is nothing to decide or it cannot be read, or when the Door decided first or
        the approval lapsed: the turn is then a normal one.

        The history is the record's, not the session's: it ends in the open calls being
        answered, where the session may hold a text turn after them.
        """
        if approval_id is None or who != "sir":
            return None
        run = await claimable_run(self._redis, approval_id)
        if run is None or run.session_id != request.session_id:
            return None
        try:
            history = ModelMessagesTypeAdapter.validate_json(run.messages)
        except ValueError:
            logger.warning(
                "Approval %s holds a history that no longer reads; a normal turn",
                approval_id,
                exc_info=True,
            )
            return None
        decision: Decision = "other"
        if self._approval is not None:
            decision = await self._approval.classify(run.calls, request.content)
        claimed = await claim_pending(self._redis, approval_id)
        if claimed is None:
            logger.info("Approval %s was settled before this reply could claim it", approval_id)
            return None
        if claimed != run:
            # Never expected: a record is written once (SET NX) and never changed.
            logger.warning("Approval %s changed under the reply; nothing runs", approval_id)
            return None
        logger.info("Reply decides approval %s: %s", approval_id, decision)
        return _Resume(approval_id, history, results_for(run, decision))

    async def _answer_reflex_ask(
        self, request: UserRequest, who: str, history: list[ModelMessage]
    ) -> str | None:
        """The reply to *request* when it answers the ask Reflex has open, else ``None``.

        Runs only when no parked run in this conversation took the reply (``_resume_for``).
        Only the owner answers, on a channel an ask's notification reaches
        (``ASK_REPLY_CHANNELS``, never speech), and only with a classifier: without one every
        reply moves on. An ask past its deadline is settled first. A reply answers the ask only
        while Alfred has not spoken in this conversation since ``ASK_LOOKBACK_SECONDS`` before
        the ask went out: after that, a "yes" answers whatever Alfred said, not Reflex (Alfred's
        own question, asked just before the ask, is the likelier one; *history* is the
        conversation's, and each response in it carries the time it was received). A
        conversation Alfred has not spoken in since, or none at all, still counts: the ask
        reached the owner elsewhere.
        The parked action is read, not claimed, and the reply classified against it; ``decide``
        then claims it, so a Door tap and a reply can never both answer. ``None`` is a normal
        turn: no open ask, Alfred spoke since, a pending record that no longer reads or is not
        a Reflex ask, a reply that is neither yes nor no, or an answer that lost to another.
        """
        if who != "sir" or request.channel not in ASK_REPLY_CHANNELS or self._approval is None:
            return None
        await settle_expired(self._redis, now=datetime.now(UTC))
        ask = await read_open_ask(self._redis)
        if ask is None:
            return None
        if spoke_since(history, ask.asked_at - timedelta(seconds=ASK_LOOKBACK_SECONDS)):
            logger.info(
                "Reply is not read against Reflex ask %s: Alfred spoke in this conversation"
                " around it",
                ask.ask_id,
            )
            return None
        try:
            record = await read_pending(self._redis, ask.ask_id)
        except ValueError:
            logger.warning("Reflex ask %s holds a record that no longer reads", ask.ask_id)
            return None
        # Only a Reflex ask is answered in words: a critical action the router held for another
        # source is approved at the Door alone, never by free text.
        if not isinstance(record, ParkedAction) or record.action.source != ASK_SOURCE:
            return None
        action = record.action
        call = ParkedCall(
            tool_call_id=ask.ask_id,
            tool_name=action.tool_name,
            target_service=action.target_service,
            parameters=action.parameters,
            reason=action.reason or "",
        )
        verdict = await self._approval.classify([call], request.content)
        if verdict == "other":
            return None
        logger.info("Reply decides Reflex ask %s: %s", ask.ask_id, verdict)
        if not await decide(self._redis, ask.ask_id, verdict, channel=request.channel):
            return None
        return ASK_CONFIRMED_REPLY if verdict == "approve" else ASK_DECLINED_REPLY

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
        deps: TurnDeps,
        history: list[ModelMessage],
        usage: RunUsage,
        *,
        prompt: str | None,
        said: str,
        results: DeferredToolResults | None = None,
    ) -> tuple[str, Mood, list[ModelMessage], DeferredToolRequests | None]:
        """One agent run: its reply, its mood, the session's history after it, and the calls
        it deferred for approval, if it ended waiting on them (its reply is then empty).

        *prompt* is the turn's words, None when the run only takes *results* (a Door
        decision). A finished run's history is ``all_messages()``, compacted during the
        run. A run that ends without its own answer leaves *history* plus the turn as text,
        *said* and the reply, never its half-finished messages. *usage* accumulates across
        it, for the note.

        A run that resumes (*results*) takes *history* as it is: its open calls are the
        ones being answered. Any other run first closes open calls as interrupted
        (``repair_messages``, a no-op on a history without them): a parked run's history
        ends in its open call, and Pydantic AI refuses a new prompt after it.
        """
        if results is None:
            history = repair_messages(history)
        model = self._model
        if model is None:
            return self._ended(history, said, NOT_CONFIGURED_REPLY, "neutral")
        try:
            with (
                langfuse_attributes(
                    session_id=deps.session_id, user_id=deps.who, tags=[deps.channel]
                ),
                linked_prompts(*deps.prompts.refs()),
            ):
                result = await self._agent.run(
                    prompt,
                    deps=deps,
                    message_history=history,
                    deferred_tool_results=results,
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
            reply = await self._budget_reply(deps.request_id)
            return self._ended(history, said, reply, "concerned")
        except UsageLimitExceeded:
            logger.warning("System 2 used its %d model requests without answering", REQUEST_LIMIT)
            return self._ended(history, said, DELIBERATING_REPLY, "neutral")
        except UnexpectedModelBehavior as exc:
            # Retries exhausted, blank replies, or the token limit hit before any output.
            # ModelAPIError (HTTP, connection, timeout) is a sibling, not a subclass, so a
            # provider failure still raises and the request is retried.
            logger.warning("System 2 gave up on the model's replies: %s", exc)
            return self._ended(history, said, DELIBERATING_REPLY, "neutral")

    @staticmethod
    def _ended(
        history: list[ModelMessage], said: str, text: str, mood: Mood
    ) -> tuple[str, Mood, list[ModelMessage], None]:
        """A turn answered with *text* instead of the run's own answer."""
        return text, mood, [*history, *text_turn(said, text)], None

    async def _budget_reply(self, request_id: str) -> str:
        """The budget reply for a run the budget stopped partway. If the turn already
        acted, say what it did first (``ledger_reply``), so a light that went on is never
        answered as though nothing happened; an unreadable ledger gets the plain reply."""
        try:
            effects = await EffectLedger(self._redis).read(request_id)
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

    async def record_partial_approval(self, decision: ApprovalDecision, text: str) -> None:
        """Store a Door decision answered from its side-effect ledger (*text*) in the
        record's session, as the decision's marker and *text*, as ``record_partial_turn``
        does for a request. A failed write is logged, not raised."""
        run = decision.run
        try:
            who = IdentityResult.model_validate(run.identity).identity
            await self._session_mgr.append_text_turn(
                run.session_id, who, _marker(decision.decision), text
            )
        except Exception:
            logger.warning("Failed to store a partial Door decision in the session", exc_info=True)

    @staticmethod
    def failure_reply(request: UserRequest, text: str) -> AlfredResponse:
        """The answer to a turn that could not finish: a normal reply's channel, session,
        source and ``in_reply_to``, no actions, concerned."""
        return ConsciousEngine._reply(request, text, [], mood="concerned")

    @staticmethod
    def approval_failure_reply(run: ParkedRun, text: str) -> AlfredResponse:
        """The answer to a Door decision that could not finish: a follow-up to the request
        that parked *run*, no actions, concerned."""
        return ConsciousEngine._followup(run, text, [], mood="concerned")

    @staticmethod
    def _followup(
        run: ParkedRun, text: str, actions: list[str], *, mood: Mood = "neutral"
    ) -> AlfredResponse:
        """A reply nobody is waiting on, to the request that parked *run*."""
        return AlfredResponse(
            source=SOURCE,
            channel=run.channel,
            session_id=run.session_id,
            text=text,
            actions_taken=actions,
            mood=mood,
            in_reply_to=run.request_event_id,
            followup=True,
        )

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


def _marker(decision: Literal["approve", "deny"]) -> str:
    """The owner's side of a Door decision in the session: the Door sends no words."""
    return APPROVED_MARKER if decision == "approve" else DECLINED_MARKER
