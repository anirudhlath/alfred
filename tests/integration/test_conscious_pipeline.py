"""Integration test — full Conscious Engine pipeline with all wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from bus.schemas.events import UserRequest
from core.conscious.context_assembler import ContextAssembler
from core.conscious.cost import CostTracker
from core.conscious.engine import ConsciousEngine
from core.conscious.identity import IdentityGate
from core.conscious.memory_reader import MemoryReader
from core.conscious.session import SessionManager


def _make_redis_mock() -> AsyncMock:
    """Build an AsyncMock Redis that satisfies SessionManager + CostTracker."""
    redis = AsyncMock()
    # SessionManager.get_or_create calls hgetall → empty dict = new session
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock(return_value=None)
    redis.hget = AsyncMock(return_value=None)
    redis.expire = AsyncMock(return_value=None)
    # CostTracker._get_state calls redis.get → None = new day
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=None)
    # Scratchpad write
    redis.lpush = AsyncMock(return_value=None)
    return redis


def _make_llm_response(text: str) -> MagicMock:
    """Build a mock litellm response with given text and no tool calls."""
    message = MagicMock()
    message.content = text
    message.tool_calls = None

    choice = MagicMock()
    choice.message = message

    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 20

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


@pytest.fixture()
def full_engine(tmp_path: Path) -> ConsciousEngine:
    """Build a fully-wired ConsciousEngine with mocked externals."""
    prefs = tmp_path / "preferences"
    profile = tmp_path / "profile"
    prefs.mkdir()
    profile.mkdir()
    (prefs / "personal.md").write_text(
        "---\ndomain: general\nupdated: 2026-03-19\nconfidence: manual\n---\n\n"
        "# Personal\n\n- Wake time: 07:30\n- Dietary: vegetarian\n"
    )
    (profile / "proactivity.md").write_text(
        "---\ndomain: general\nupdated: 2026-03-19\nconfidence: manual\n---\n\n"
        "# Proactivity Level\n\n- Level: moderate\n"
    )

    redis = _make_redis_mock()
    return ConsciousEngine(
        redis=redis,
        identity_gate=IdentityGate(registered_phone="+1234567890"),
        session_mgr=SessionManager(redis=redis, timeout_minutes=30),
        cost_tracker=CostTracker(redis=redis, daily_cap_usd=5.0),
        context_assembler=ContextAssembler(),
        domain_router=AsyncMock(),
        tool_registry=AsyncMock(get_tools=AsyncMock(return_value=[])),
        context_reader=AsyncMock(get_rendered_context=AsyncMock(return_value="")),
        memory_reader=MemoryReader(
            preferences_dir=prefs,
            profile_dir=profile,
            default_proactivity="opinionated",
        ),
        claude_model="test-model",
        claude_api_key="test-key",
    )


def _make_request(
    identity_claim: str = "sir",
    content: str = "Good morning",
    session_id: str = "s1",
) -> UserRequest:
    return UserRequest(
        source="test",
        channel="web_pwa",
        session_id=session_id,
        identity_claim=identity_claim,
        content_type="text",
        content=content,
    )


@pytest.mark.asyncio
async def test_full_pipeline_sir_gets_preferences(full_engine: ConsciousEngine) -> None:
    """Sir should see preferences and proactivity level in the system prompt."""
    request = _make_request(identity_claim="sir", content="Good morning")
    mock_response = _make_llm_response("Good morning, sir.")

    with patch("litellm.acompletion", return_value=mock_response) as mock_llm:
        response = await full_engine.process_request(request)

    # Verify the system prompt includes preferences
    call_kwargs = dict(mock_llm.call_args.kwargs)
    system_msg: str = call_kwargs["messages"][0]["content"]
    assert "Wake time: 07:30" in system_msg
    assert "vegetarian" in system_msg
    assert "moderate" in system_msg  # proactivity level from profile

    # Verify scratchpad was written
    full_engine._redis.lpush.assert_called()  # type: ignore[attr-defined]

    assert response.text == "Good morning, sir."


@pytest.mark.asyncio
async def test_full_pipeline_guest_no_preferences(full_engine: ConsciousEngine) -> None:
    """Guest should NOT see personal preferences in system prompt."""
    request = _make_request(identity_claim="guest", content="Hello", session_id="s2")
    mock_response = _make_llm_response("Good evening.")

    with patch("litellm.acompletion", return_value=mock_response) as mock_llm:
        response = await full_engine.process_request(request)

    system_msg: str = mock_llm.call_args.kwargs["messages"][0]["content"]
    # Guest prompt must NOT contain personal info
    assert "Wake time" not in system_msg
    assert "vegetarian" not in system_msg
    # Guest prompt must NOT contain proactivity level
    assert "Proactivity Level" not in system_msg

    assert response.text == "Good evening."


@pytest.mark.asyncio
async def test_full_pipeline_session_history_forwarded(full_engine: ConsciousEngine) -> None:
    """Session append_turn should be called with user + assistant content."""
    request = _make_request(identity_claim="sir", content="What time is it?")
    mock_response = _make_llm_response("It is currently 3pm, sir.")

    with patch("litellm.acompletion", return_value=mock_response):
        await full_engine.process_request(request)

    # SessionManager.append_turn writes via redis.hget/hset
    # Verify hset was called (session creation + append turns)
    assert full_engine._redis.hset.call_count >= 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_full_pipeline_cost_recorded(full_engine: ConsciousEngine) -> None:
    """Cost tracker should record spend after LLM call."""
    request = _make_request(identity_claim="sir", content="Hello")
    mock_response = _make_llm_response("Hello, sir.")

    with patch("litellm.acompletion", return_value=mock_response):
        await full_engine.process_request(request)

    # CostTracker._save_state writes via redis.set
    full_engine._redis.set.assert_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_full_pipeline_scratchpad_observation_format(
    full_engine: ConsciousEngine,
) -> None:
    """Scratchpad observation should contain request summary and token counts."""
    request = _make_request(identity_claim="sir", content="Turn on the lights")
    mock_response = _make_llm_response("Done, sir.")

    with patch("litellm.acompletion", return_value=mock_response):
        await full_engine.process_request(request)

    lpush_call = full_engine._redis.lpush.call_args  # type: ignore[attr-defined]
    observation: str = lpush_call.args[1]
    assert "[conscious]" in observation
    assert "Turn on the lights" in observation
    assert "tokens=100+20" in observation
