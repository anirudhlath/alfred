"""Tests for ConsciousEngine."""

from __future__ import annotations

import datetime as _dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.engine import MAX_ITERATIONS, ConsciousEngine
from core.identity.schemas import IdentityResult
from core.memory.schemas import RoutineSpec
from core.reflex.tool_registry import ToolInfo


def _make_request(**overrides: object) -> UserRequest:
    defaults = {
        "source": "web-pwa",
        "channel": "web_pwa",
        "session_id": "sess-1",
        "identity_claim": "sir",
        "authenticated": True,
        "content_type": "text",
        "content": "Hello",
    }
    defaults.update(overrides)
    return UserRequest(**defaults)


def _sir_identity() -> IdentityResult:
    return IdentityResult(
        identity="sir",
        confidence=0.99,
        method="webauthn",
        factors=["webauthn"],
        risk_clearance="high",
    )


@pytest.fixture
def mock_deps() -> dict[str, AsyncMock | MagicMock]:
    deps: dict[str, AsyncMock | MagicMock] = {
        "redis": AsyncMock(),
        "identity_gate": MagicMock(),
        "session_mgr": AsyncMock(),
        "cost_tracker": AsyncMock(),
        "context_assembler": MagicMock(),
        "domain_router": AsyncMock(),
        "tool_registry": AsyncMock(),
        "context_reader": AsyncMock(),
    }
    # Common defaults
    deps["identity_gate"].resolve.return_value = _sir_identity()
    deps["session_mgr"].get_or_create.return_value = {"channel": "web_pwa", "history": []}
    deps["cost_tracker"].is_budget_exceeded.return_value = False
    deps["context_assembler"].assemble.return_value = "You are Alfred."
    deps["tool_registry"].get_tools.return_value = []
    deps["context_reader"].get_rendered_context.return_value = ""
    return deps


@pytest.mark.asyncio
async def test_process_request_basic(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    engine = ConsciousEngine(**mock_deps)

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Good evening, sir.", [], 100, 50)
        response = await engine.process_request(_make_request())

    assert isinstance(response, AlfredResponse)
    assert response.text == "Good evening, sir."


@pytest.mark.asyncio
async def test_process_request_persists_valid_timezone(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """A request carrying a valid timezone is persisted at the domain boundary."""
    from shared.streams import USER_TIMEZONE_KEY

    # get() → None so set_user_timezone's change-guard always writes.
    mock_deps["redis"].get = AsyncMock(return_value=None)
    engine = ConsciousEngine(**mock_deps)

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Good evening, sir.", [], 100, 50)
        await engine.process_request(_make_request(timezone="America/Denver"))

    mock_deps["redis"].set.assert_any_await(USER_TIMEZONE_KEY, "America/Denver")


@pytest.mark.asyncio
async def test_budget_exceeded_returns_fallback(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    mock_deps["cost_tracker"].is_budget_exceeded.return_value = True
    engine = ConsciousEngine(**mock_deps)

    response = await engine.process_request(_make_request(content="Good morning"))
    assert isinstance(response, AlfredResponse)
    assert "budget" in response.text.lower() or "reduced" in response.text.lower()


@pytest.mark.asyncio
async def test_multi_turn_tool_use(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """Agentic loop: LLM calls a tool, gets result, then responds."""
    tool = ToolInfo(
        name="smart_home.get_lights",
        description="Get light state",
        parameters={},
        feature_name="home",
        feature_description="Home control",
        target_service="home-service",
    )
    mock_deps["tool_registry"].get_tools.return_value = [tool]

    action_result = MagicMock()
    action_result.status = "success"
    action_result.result = {"lights": "on"}
    mock_deps["domain_router"].route.return_value = action_result

    engine = ConsciousEngine(**mock_deps)

    tool_call = [{"id": "tc-1", "name": "smart_home.get_lights", "input": {}}]

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        # First call: LLM uses a tool. Second call: LLM responds with text.
        mock_llm.side_effect = [
            ("Let me check...", tool_call, 100, 50),
            ("The lights are on, sir.", [], 150, 60),
        ]
        response = await engine.process_request(_make_request(content="Are the lights on?"))

    assert response.text == "The lights are on, sir."
    assert "smart_home.get_lights" in response.actions_taken
    assert mock_llm.call_count == 2


@pytest.mark.asyncio
async def test_tool_not_found_in_registry(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """Tool call for a tool not in registry returns error result."""
    mock_deps["tool_registry"].get_tools.return_value = []  # empty registry
    engine = ConsciousEngine(**mock_deps)

    results = await engine._execute_tool_calls(
        [{"id": "tc-1", "name": "nonexistent.tool", "input": {}}], tools=[]
    )

    assert len(results) == 1
    assert results[0]["type"] == "tool_result"
    assert "not found" in results[0]["content"]


@pytest.mark.asyncio
async def test_max_iterations_fallback(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """When tool calls never resolve, engine hits MAX_ITERATIONS and returns fallback."""
    tool = ToolInfo(
        name="some.tool",
        description="A tool",
        parameters={},
        feature_name="test",
        feature_description="Test",
        target_service="test-service",
    )
    mock_deps["tool_registry"].get_tools.return_value = [tool]

    action_result = MagicMock()
    action_result.status = "success"
    action_result.result = {"ok": True}
    mock_deps["domain_router"].route.return_value = action_result

    engine = ConsciousEngine(**mock_deps)

    # LLM always returns a tool call, never a final text-only response
    tool_call = [{"id": "tc-1", "name": "some.tool", "input": {}}]
    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("", tool_call, 100, 50)
        response = await engine.process_request(_make_request())

    assert mock_llm.call_count == MAX_ITERATIONS
    assert "apologize" in response.text.lower() or "deliberating" in response.text.lower()


def test_tools_to_openai_format(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """Verify ToolInfo → OpenAI function-calling format conversion."""
    engine = ConsciousEngine(**mock_deps)
    tools = [
        ToolInfo(
            name="smart_home.set_light",
            description="Set light brightness",
            parameters={
                "room": {"type": "str", "description": "Room name"},
                "level": {"type": "int", "description": "Brightness", "default": 100},
            },
            feature_name="home",
            feature_description="Home",
            target_service="home-service",
        ),
    ]

    result = engine._tools_to_openai_format(tools)

    assert len(result) == 1
    assert result[0]["type"] == "function"
    func = result[0]["function"]
    # Dots sanitized to underscores for OpenAI compatibility
    assert func["name"] == "smart_home_set_light"
    schema = func["parameters"]
    assert "room" in schema["properties"]
    assert "level" in schema["properties"]
    # Python types mapped to JSON Schema types
    assert schema["properties"]["room"]["type"] == "string"
    assert schema["properties"]["level"]["type"] == "integer"
    # room has no default → required; level has default → not required
    assert "room" in schema["required"]
    assert "level" not in schema["required"]


def test_tool_name_sanitization(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """Tool names with dots are sanitized for OpenAI and reversed back."""
    engine = ConsciousEngine(**mock_deps)
    assert engine._sanitize_tool_name("lighting.dim_lights") == "lighting_dim_lights"

    tools = [
        ToolInfo(
            name="lighting.dim_lights",
            description="Dim",
            parameters={},
            feature_name="home",
            feature_description="Home",
            target_service="home-service",
        ),
    ]
    assert engine._unsanitize_tool_name("lighting_dim_lights", tools) == "lighting.dim_lights"


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


def test_build_routine_hint_returns_empty_without_routines(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """When routine_store is None, _build_routine_hint returns empty string."""
    engine = ConsciousEngine(**mock_deps)
    # routine_store not set → _routines is None
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now)
    assert result == ""


def test_build_routine_hint_matches_time_pattern(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Candidate routine with matching time pattern should produce a hint."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="20:00 daily")]

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now)

    assert "evening_dim" in result
    assert "[routine-suggestion]" in result
    routine_store.save.assert_called_once()


def test_build_routine_hint_skips_within_cooldown(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Routine suggested 2 hours ago (within 24h cooldown) should be skipped."""
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    recent_suggestion = now - _dt.timedelta(hours=2)
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [
        _make_routine(trigger_pattern="20:00 daily", last_suggested=recent_suggestion)
    ]

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)
    result = engine._build_routine_hint(now)

    assert result == ""
    routine_store.save.assert_not_called()


def test_build_routine_hint_suggests_after_cooldown(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Routine suggested 25 hours ago (outside cooldown) should be suggested again."""
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    old_suggestion = now - _dt.timedelta(hours=25)
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [
        _make_routine(trigger_pattern="20:00 daily", last_suggested=old_suggestion)
    ]

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)
    result = engine._build_routine_hint(now)

    assert "evening_dim" in result
    routine_store.save.assert_called_once()


def test_build_routine_hint_no_match_for_wrong_time(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Evening pattern should not match when it is morning."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="evening")]

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)
    # 8am → not evening
    now = _dt.datetime(2026, 3, 24, 8, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now)

    assert result == ""


def test_build_routine_hint_morning_pattern_matches_morning(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Morning pattern should match when it is morning."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="weekday morning")]

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)
    # 9am Monday
    now = _dt.datetime(2026, 3, 23, 9, 0, 0, tzinfo=_dt.UTC)  # Monday
    result = engine._build_routine_hint(now)

    assert "evening_dim" in result  # routine name


@pytest.mark.asyncio
async def test_process_request_injects_routine_hint(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """process_request should append routine hint to system prompt when a candidate matches."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="20:00 daily")]

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Good evening, sir.", [], 100, 50)

        # 8pm → matches "20:00 daily"
        fixed_now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
        with patch("core.conscious.engine.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now

            await engine.process_request(_make_request())

    # The system_prompt passed to _call_llm should contain the routine hint
    call_kwargs = mock_llm.call_args
    system_prompt_arg = call_kwargs[0][0]  # first positional arg
    assert "[routine-suggestion]" in system_prompt_arg
    assert "evening_dim" in system_prompt_arg


def test_build_routine_hint_includes_steps_and_confidence(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
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

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now)

    assert "Dim lights" in result
    assert "75%" in result


def test_build_routine_hint_empty_steps_shows_na(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Hint should show N/A for steps when the routine has no steps defined."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="20:00 daily")]

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    result = engine._build_routine_hint(now)

    assert "N/A" in result
    assert "80%" in result


# ---------------------------------------------------------------------------
# check_routine_suggestions tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_routine_suggestions_publishes_notification(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """check_routine_suggestions should publish NORMAL notification for matching candidates."""
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [_make_routine(trigger_pattern="20:00 daily")]

    notifier = AsyncMock()
    notifier.publish = AsyncMock()

    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)

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
async def test_check_routine_suggestions_respects_cooldown(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """check_routine_suggestions should skip routines within cooldown."""
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    recent = now - _dt.timedelta(hours=2)
    routine_store = MagicMock()
    routine_store.list_by_state.return_value = [
        _make_routine(trigger_pattern="20:00 daily", last_suggested=recent)
    ]

    notifier = AsyncMock()
    engine = ConsciousEngine(**mock_deps, routine_store=routine_store)

    await engine.check_routine_suggestions(now=now, notifier=notifier)

    notifier.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_routine_suggestions_no_routines(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """check_routine_suggestions should be a no-op without a routine store."""
    engine = ConsciousEngine(**mock_deps)
    notifier = AsyncMock()
    now = _dt.datetime(2026, 3, 24, 20, 0, 0, tzinfo=_dt.UTC)
    await engine.check_routine_suggestions(now=now, notifier=notifier)
    notifier.publish.assert_not_awaited()
