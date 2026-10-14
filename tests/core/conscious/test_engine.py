"""The Conscious Engine: one request in, one reply out, the System 2 agent in between."""

from __future__ import annotations

import datetime as _dt
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.agent import REQUEST_LIMIT, build_system2_agent, system2_toolsets
from core.conscious.context_assembler import ContextAssembler
from core.conscious.engine import (
    BUDGET_REPLY,
    DELIBERATING_REPLY,
    NOT_CONFIGURED_REPLY,
    ConsciousConfig,
    ConsciousDeps,
    ConsciousEngine,
    session_messages,
)
from core.conscious.tools.catalog import TOOL_RETRIES
from core.memory.schemas import RoutineSpec
from core.memory.vector_store import ContextMetadata, SearchResult
from core.reflex.tool_registry import ToolInfo
from shared.agents import model_key
from shared.config import DEFAULT_INVOLUNTARY_RECALL_THRESHOLD
from tests.core.conscious.helpers import identity
from tests.llm_fakes import Script, tool_call

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def integrations(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """No real adapter is asked for its capabilities."""
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr("core.conscious.engine.fetch_integration_tools", fetch)
    return fetch


def _request(**overrides: Any) -> UserRequest:
    fields: dict[str, Any] = {
        "source": "web-pwa",
        "channel": "web_pwa",
        "session_id": "sess-1",
        "identity_claim": "sir",
        "authenticated": True,
        "content_type": "text",
        "content": "Hello",
    }
    fields.update(overrides)
    return UserRequest(**fields)


def _deps(script: Script | None = None, *, who: str = "sir", **overrides: Any) -> ConsciousDeps:
    """Mocked services around a real System 2 agent; no script means no model."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    identity_gate = MagicMock()
    identity_gate.resolve.return_value = identity(who)  # type: ignore[arg-type]
    session_mgr = AsyncMock()
    session_mgr.get_or_create.return_value = {"channel": "web_pwa", "history": []}
    cost = AsyncMock()
    cost.is_budget_exceeded.return_value = False
    registry = AsyncMock()
    registry.get_tools.return_value = []
    fields: dict[str, Any] = {
        "redis": redis,
        "identity_gate": identity_gate,
        "session_mgr": session_mgr,
        "cost_tracker": cost,
        "domain_router": AsyncMock(),
        "tool_registry": registry,
        "context_reader": AsyncMock(),
        "agent": build_system2_agent(
            toolsets=system2_toolsets(None),
            assembler=ContextAssembler(),
            max_tokens=256,
            timeout_s=5.0,
        ),
        "model": script.model if script is not None else None,
    }
    fields.update(overrides)
    return ConsciousDeps(**fields)


def _info(name: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} does a thing",
        feature_name=name.split(".")[0],
        feature_description="Home control",
        target_service="home-service",
        risk="benign",
        input_schema={
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
    )


def _router() -> AsyncMock:
    router = AsyncMock()
    router.route.return_value = MagicMock(status="success", result={"ok": True}, error=None)
    return router


def _memory(content: str) -> SearchResult:
    return SearchResult(
        id="m1",
        score=0.8,
        content=content,
        semantic_key=content,
        metadata=ContextMetadata(
            type="semantic",
            source="preferences.md",
            entities="",
            timestamp=0.0,
            significance=1.0,
            retrieval_count=0,
        ),
    )


def _user_text(message: ModelRequest) -> str:
    return next(p.content for p in message.parts if isinstance(p, UserPromptPart))


def test_the_replies_are_verbatim() -> None:
    assert BUDGET_REPLY == (
        "I'm afraid we've reached the daily budget, sir. I'm operating in reduced capacity"
        " — ambient actions continue, but I'll need to defer complex requests until tomorrow."
    )
    assert DELIBERATING_REPLY == (
        "I apologize, sir — I've been deliberating too long. Let me try a more direct approach."
    )
    assert NOT_CONFIGURED_REPLY == (
        "I'm afraid my connection to the thinking engine is not configured, sir."
    )


def test_session_messages_keep_user_and_assistant_text_only() -> None:
    messages = session_messages(
        [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Good evening, sir."},
            {"role": "system", "content": "not a turn"},
        ]
    )
    assert len(messages) == 2
    assert isinstance(messages[0], ModelRequest) and _user_text(messages[0]) == "Hi"
    assert isinstance(messages[1], ModelResponse)
    assert messages[1].parts == [TextPart("Good evening, sir.")]


@pytest.mark.asyncio
async def test_a_turn_returns_the_agents_reply_and_stores_both_sides() -> None:
    script = Script("Good morning, sir.")
    deps = _deps(script)

    response = await ConsciousEngine(deps=deps).process_request(_request(content="Good morning"))

    assert isinstance(response, AlfredResponse)
    assert (response.text, response.source, response.mood, response.actions_taken) == (
        "Good morning, sir.",
        "conscious-engine",
        "neutral",
        [],
    )
    deps.session_mgr.append_turn.assert_has_awaits(  # type: ignore[attr-defined]
        [call("sess-1", "user", "Good morning"), call("sess-1", "assistant", "Good morning, sir.")]
    )


@pytest.mark.asyncio
async def test_no_model_answers_not_configured() -> None:
    deps = _deps()

    response = await ConsciousEngine(deps=deps).process_request(_request())

    assert response.text == NOT_CONFIGURED_REPLY
    deps.cost_tracker.record_spend.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_an_exceeded_budget_answers_without_a_run() -> None:
    script = Script("never sent")
    deps = _deps(script)
    deps.cost_tracker.is_budget_exceeded.return_value = True  # type: ignore[attr-defined]

    response = await ConsciousEngine(deps=deps).process_request(_request())

    assert (response.text, response.mood) == (BUDGET_REPLY, "concerned")
    assert script.call_count == 0
    deps.session_mgr.get_or_create.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_request_limit_ends_the_turn_with_the_deliberating_reply() -> None:
    registry = AsyncMock()
    registry.get_tools.return_value = [_info("home.get_lights")]
    calls = [
        tool_call("home_get_lights", {"entity_id": "light.desk"}) for _ in range(REQUEST_LIMIT)
    ]
    script = Script(*calls)
    deps = _deps(script, tool_registry=registry, domain_router=_router())

    response = await ConsciousEngine(deps=deps).process_request(_request())

    assert response.text == DELIBERATING_REPLY
    assert script.call_count == REQUEST_LIMIT
    assert response.actions_taken == ["home.get_lights"] * REQUEST_LIMIT
    deps.cost_tracker.record_spend.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_blank_replies_past_the_output_retries_end_with_the_deliberating_reply() -> None:
    script = Script("", "", "")
    deps = _deps(script)

    response = await ConsciousEngine(deps=deps).process_request(_request())

    assert response.text == DELIBERATING_REPLY
    assert script.call_count == 3
    deps.cost_tracker.record_spend.assert_awaited_once()  # type: ignore[attr-defined]
    deps.session_mgr.append_turn.assert_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_tool_call_failing_past_its_retries_ends_with_the_deliberating_reply() -> None:
    registry = AsyncMock()
    registry.get_tools.return_value = [_info("home.get_lights")]
    script = Script(*[tool_call("home_get_lights", {}) for _ in range(TOOL_RETRIES + 1)])
    deps = _deps(script, tool_registry=registry, domain_router=_router())

    response = await ConsciousEngine(deps=deps).process_request(_request())

    assert response.text == DELIBERATING_REPLY
    assert script.call_count == TOOL_RETRIES + 1
    assert response.actions_taken == []
    deps.domain_router.route.assert_not_awaited()  # type: ignore[attr-defined]
    deps.cost_tracker.record_spend.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_provider_error_still_raises_so_the_request_is_retried() -> None:
    deps = _deps(Script(ModelHTTPError(status_code=503, model_name="test")))

    with pytest.raises(ModelHTTPError):
        await ConsciousEngine(deps=deps).process_request(_request())

    deps.cost_tracker.record_spend.assert_awaited_once()  # type: ignore[attr-defined]
    deps.session_mgr.append_turn.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_failed_spend_write_still_returns_the_reply() -> None:
    deps = _deps(Script("Hello, sir."))
    deps.cost_tracker.record_spend.side_effect = ConnectionError("redis blip")  # type: ignore[attr-defined]

    response = await ConsciousEngine(deps=deps).process_request(_request())

    assert response.text == "Hello, sir."
    deps.session_mgr.append_turn.assert_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_failed_run_records_its_spend_and_stores_no_turn() -> None:
    deps = _deps(Script(RuntimeError("provider down")))

    with pytest.raises(RuntimeError, match="provider down"):
        await ConsciousEngine(deps=deps).process_request(_request())

    deps.cost_tracker.record_spend.assert_awaited_once()  # type: ignore[attr-defined]
    deps.session_mgr.append_turn.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_valid_client_timezone_is_persisted_and_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = AsyncMock()
    monkeypatch.setattr("core.conscious.engine.set_user_timezone", saved)
    script = Script("ok")
    deps = _deps(script)

    await ConsciousEngine(deps=deps).process_request(_request(timezone="America/Denver"))

    saved.assert_awaited_once_with(deps.redis, "America/Denver")
    assert "(America/Denver)" in script.instructions()


@pytest.mark.asyncio
async def test_an_invalid_timezone_falls_back_to_the_stored_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = AsyncMock()
    monkeypatch.setattr("core.conscious.engine.set_user_timezone", saved)
    script = Script("ok")
    deps = _deps(script)
    deps.redis.get = AsyncMock(return_value=b"Europe/Paris")  # type: ignore[method-assign]

    await ConsciousEngine(deps=deps).process_request(_request(timezone="Not/AZone"))

    saved.assert_not_awaited()
    assert "(Europe/Paris)" in script.instructions()


@pytest.mark.asyncio
async def test_the_session_history_is_the_runs_message_history() -> None:
    script = Script("It is 3pm, sir.")
    deps = _deps(script)
    deps.session_mgr.get_or_create.return_value = {  # type: ignore[attr-defined]
        "channel": "web_pwa",
        "history": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Good evening, sir."},
        ],
    }

    await ConsciousEngine(deps=deps).process_request(_request(content="What time is it?"))

    sent = script.requests[0][0]
    assert [type(m) for m in sent] == [ModelRequest, ModelResponse, ModelRequest]
    assert _user_text(sent[0]) == "Hi"
    assert sent[1].parts == [TextPart("Good evening, sir.")]
    assert _user_text(sent[2]) == "What time is it?"


@pytest.mark.asyncio
async def test_spend_and_the_scratchpad_note_use_the_runs_token_counts() -> None:
    reply = ModelResponse(
        parts=[TextPart("Done, sir.")], usage=RequestUsage(input_tokens=100, output_tokens=20)
    )
    script = Script(reply)
    deps = _deps(script)

    await ConsciousEngine(deps=deps).process_request(_request(content="Turn on the lights"))

    deps.cost_tracker.record_spend.assert_awaited_once_with(  # type: ignore[attr-defined]
        100, 20, model_key(script.model)
    )
    note = deps.redis.lpush.await_args.args[1]  # type: ignore[attr-defined]
    assert "[conscious] user='Turn on the lights' → 10 chars" in note
    assert note.endswith("(actions=none, tokens=100+20)")


@pytest.mark.asyncio
async def test_the_calls_the_catalog_dispatched_are_the_responses_actions() -> None:
    registry = AsyncMock()
    registry.get_tools.return_value = [_info("home.light_on")]
    script = Script(tool_call("home_light_on", {"entity_id": "light.desk"}), "Done, sir.")
    deps = _deps(script, tool_registry=registry, domain_router=_router())

    response = await ConsciousEngine(deps=deps).process_request(_request())

    assert response.actions_taken == ["home.light_on"]
    assert "(actions=home.light_on, tokens=" in deps.redis.lpush.await_args.args[1]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_run_carries_langfuse_session_user_and_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    @contextmanager
    def _attributes(**kwargs: Any) -> Iterator[None]:
        seen.update(kwargs)
        yield

    monkeypatch.setattr("core.conscious.engine.langfuse_attributes", _attributes)

    await ConsciousEngine(deps=_deps(Script("hi"))).process_request(
        _request(session_id="s9", channel="signal")
    )

    assert seen == {"session_id": "s9", "user_id": "sir", "tags": ["signal"]}


@pytest.mark.asyncio
async def test_involuntary_recall_reaches_the_instructions() -> None:
    index = AsyncMock()
    index.search_text.return_value = [_memory("Sir prefers dim lighting after 8pm")]
    script = Script("ok")
    deps = _deps(script, context_index=index)

    await ConsciousEngine(deps=deps).process_request(_request(content="Lights?"))

    index.search_text.assert_awaited_once_with(
        "Lights?", limit=10, min_similarity=DEFAULT_INVOLUNTARY_RECALL_THRESHOLD
    )
    assert "Sir prefers dim lighting after 8pm" in script.instructions()


@pytest.mark.asyncio
async def test_involuntary_recall_uses_the_configured_limit_and_floor() -> None:
    index = AsyncMock()
    index.search_text.return_value = []
    config = ConsciousConfig(involuntary_recall_limit=3, involuntary_recall_threshold=0.7)
    deps = _deps(Script("ok"), context_index=index, config=config)

    await ConsciousEngine(deps=deps).process_request(_request(content="Lights?"))

    index.search_text.assert_awaited_once_with("Lights?", limit=3, min_similarity=0.7)


@pytest.mark.asyncio
async def test_a_failed_recall_still_answers() -> None:
    index = AsyncMock()
    index.search_text.side_effect = RuntimeError("embedding service down")
    script = Script("Hello, sir.")

    response = await ConsciousEngine(deps=_deps(script, context_index=index)).process_request(
        _request()
    )

    assert response.text == "Hello, sir."
    assert "## Relevant" not in script.instructions()


@pytest.mark.asyncio
async def test_memory_tools_are_offered_to_sir_only_with_an_index() -> None:
    with_index, without = Script("hi"), Script("hi")

    await ConsciousEngine(deps=_deps(with_index, context_index=AsyncMock())).process_request(
        _request()
    )
    await ConsciousEngine(deps=_deps(without)).process_request(_request())

    assert {"memory_recall_memories", "memory_get_live_state"} <= set(with_index.offered())
    assert not [name for name in without.offered() if name.startswith("memory_")]


@pytest.mark.asyncio
async def test_integration_tools_are_listed_for_sir_only(integrations: AsyncMock) -> None:
    await ConsciousEngine(deps=_deps(Script("hi"), who="guest")).process_request(_request())
    integrations.assert_not_awaited()

    await ConsciousEngine(deps=_deps(Script("hi"))).process_request(_request())
    integrations.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_integration_listing_still_answers(integrations: AsyncMock) -> None:
    integrations.side_effect = RuntimeError("registry broken")

    response = await ConsciousEngine(deps=_deps(Script("Hello, sir."))).process_request(_request())

    assert response.text == "Hello, sir."


# ---------------------------------------------------------------------------
# Routine suggestion tests
# ---------------------------------------------------------------------------


def _make_routine(
    name: str = "evening_dim",
    trigger_pattern: str = "20:00 daily",
    state: str = "candidate",
    last_suggested: _dt.datetime | None = None,
) -> RoutineSpec:
    return RoutineSpec(
        name=name,
        trigger_pattern=trigger_pattern,
        steps=[],
        confidence=0.8,
        learned_from=["ep-1"],
        state=state,  # type: ignore[arg-type]
        last_suggested=last_suggested,
    )


def test_build_routine_hint_returns_empty_without_routines() -> None:
    """When routine_store is None, _build_routine_hint returns empty string."""
    engine = ConsciousEngine(deps=_deps())
    # routine_store not set → _routines is None
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now, "UTC")
    assert result == ""


def test_build_routine_hint_matches_time_pattern() -> None:
    """Candidate routine with matching time pattern should produce a hint."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="20:00 daily")]

    engine = ConsciousEngine(deps=_deps(routine_store=routine_store))
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now, "UTC")

    assert "evening_dim" in result
    assert "[routine-suggestion]" in result
    routine_store.save.assert_called_once()


def test_build_routine_hint_skips_within_cooldown() -> None:
    """Routine suggested 2 hours ago (within 24h cooldown) should be skipped."""
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    recent_suggestion = now - _dt.timedelta(hours=2)
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [
        _make_routine(trigger_pattern="20:00 daily", last_suggested=recent_suggestion)
    ]

    engine = ConsciousEngine(deps=_deps(routine_store=routine_store))
    result = engine._build_routine_hint(now, "UTC")

    assert result == ""
    routine_store.save.assert_not_called()


def test_build_routine_hint_suggests_after_cooldown() -> None:
    """Routine suggested 25 hours ago (outside cooldown) should be suggested again."""
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    old_suggestion = now - _dt.timedelta(hours=25)
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [
        _make_routine(trigger_pattern="20:00 daily", last_suggested=old_suggestion)
    ]

    engine = ConsciousEngine(deps=_deps(routine_store=routine_store))
    result = engine._build_routine_hint(now, "UTC")

    assert "evening_dim" in result
    routine_store.save.assert_called_once()


def test_build_routine_hint_no_match_for_wrong_time() -> None:
    """Evening pattern should not match when it is morning."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="evening")]

    engine = ConsciousEngine(deps=_deps(routine_store=routine_store))
    # 8am → not evening
    now = _dt.datetime(2026, 3, 24, 8, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now, "UTC")

    assert result == ""


def test_build_routine_hint_morning_pattern_matches_morning() -> None:
    """Morning pattern should match when it is morning."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="weekday morning")]

    engine = ConsciousEngine(deps=_deps(routine_store=routine_store))
    # 9am Monday
    now = _dt.datetime(2026, 3, 23, 9, 0, 0, tzinfo=_dt.UTC)  # Monday
    result = engine._build_routine_hint(now, "UTC")

    assert "evening_dim" in result  # routine name


def test_build_routine_hint_includes_steps_and_confidence() -> None:
    """Hint should include step descriptions and confidence percentage."""
    from core.memory.schemas import RoutineStep

    routine_store = MagicMock()
    routine_with_steps = RoutineSpec(
        name="evening_dim",
        trigger_pattern="20:00 daily",
        steps=[RoutineStep(description="Dim lights")],
        confidence=0.75,
        learned_from=["ep-1"],
        state="candidate",
    )
    routine_store.list_by_state.return_value = [routine_with_steps]

    engine = ConsciousEngine(deps=_deps(routine_store=routine_store))
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now, "UTC")

    assert "Dim lights" in result
    assert "75%" in result


def test_build_routine_hint_empty_steps_shows_na() -> None:
    """Hint should show N/A for steps when the routine has no steps defined."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="20:00 daily")]

    engine = ConsciousEngine(deps=_deps(routine_store=routine_store))
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now, "UTC")

    assert "N/A" in result
    assert "80%" in result


# ---------------------------------------------------------------------------
# check_routine_suggestions tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_routine_suggestions_publishes_notification() -> None:
    """check_routine_suggestions should publish NORMAL notification for matching candidates."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="20:00 daily")]

    notifier = AsyncMock()
    notifier.publish = AsyncMock()

    engine = ConsciousEngine(deps=_deps(routine_store=routine_store))

    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    await engine.check_routine_suggestions(now=now, notifier=notifier)

    notifier.publish.assert_awaited_once()
    call_kwargs = notifier.publish.await_args.kwargs
    assert call_kwargs["source"] == "librarian"
    assert "evening_dim" in call_kwargs["body"].lower() or "20:00" in call_kwargs["body"]

    # Verify last_suggested was updated (cooldown mechanism depends on this)
    routine_store.save.assert_called_once()
    saved = routine_store.save.call_args[0][0]
    assert saved.last_suggested == now


@pytest.mark.asyncio
async def test_check_routine_suggestions_respects_cooldown() -> None:
    """check_routine_suggestions should skip routines within cooldown."""
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    recent = now - _dt.timedelta(hours=2)
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [
        _make_routine(trigger_pattern="20:00 daily", last_suggested=recent)
    ]

    notifier = AsyncMock()
    engine = ConsciousEngine(deps=_deps(routine_store=routine_store))

    await engine.check_routine_suggestions(now=now, notifier=notifier)

    notifier.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_routine_suggestions_no_routines() -> None:
    """check_routine_suggestions should be a no-op without a routine store."""
    engine = ConsciousEngine(deps=_deps())
    notifier = AsyncMock()
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    await engine.check_routine_suggestions(now=now, notifier=notifier)
    notifier.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_request_injects_routine_hint() -> None:
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="20:00 daily")]
    script = Script("Good evening, sir.")
    deps = _deps(script, routine_store=routine_store)

    with patch("core.conscious.engine.datetime") as mock_dt:
        mock_dt.now.return_value = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
        await ConsciousEngine(deps=deps).process_request(_request())

    assert "[routine-suggestion]" in script.instructions()
    assert "evening_dim" in script.instructions()


# ── a turn answered from its side-effect ledger (#304) ───────────────────────


async def test_record_partial_turn_appends_the_turn_and_replies_concerned() -> None:
    deps = _deps()
    engine = ConsciousEngine(deps=deps)
    request = _request(content="turn on the porch light")

    reply = await engine.record_partial_turn(request, "I did some of it, sir.")

    assert (reply.text, reply.mood) == ("I did some of it, sir.", "concerned")
    assert (reply.channel, reply.session_id) == ("web_pwa", "sess-1")
    assert deps.session_mgr.append_turn.await_args_list == [  # type: ignore[attr-defined]
        call("sess-1", "user", "turn on the porch light"),
        call("sess-1", "assistant", "I did some of it, sir."),
    ]


async def test_record_partial_turn_still_replies_when_the_session_cannot_be_written() -> None:
    deps = _deps()
    deps.session_mgr.append_turn.side_effect = ConnectionError("gone")  # type: ignore[attr-defined]

    reply = await ConsciousEngine(deps=deps).record_partial_turn(_request(), "partial")

    assert reply.text == "partial"
