"""Integration test — full Conscious Engine pipeline with all wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from bus.schemas.events import UserRequest
from core.conscious.agent import build_system2_agent, system2_toolsets
from core.conscious.engine import ConsciousDeps, ConsciousEngine
from core.conscious.identity import IdentityGate
from core.conscious.session import SessionManager
from shared.spend import build_spend_limits, local_clock
from tests.llm_fakes import Script

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


def _make_redis_mock() -> AsyncMock:
    """Build an AsyncMock Redis that satisfies SessionManager."""
    redis = AsyncMock()
    # SessionManager.load calls hgetall → empty dict = new session
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock(return_value=None)
    redis.hget = AsyncMock(return_value=None)
    redis.expire = AsyncMock(return_value=None)
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=None)
    # Scratchpad write
    redis.lpush = AsyncMock(return_value=None)
    return redis


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


def _reply(text: str) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(text)], usage=RequestUsage(input_tokens=100, output_tokens=20)
    )


def _no_integrations() -> AbstractContextManager[AsyncMock]:
    return patch("core.conscious.engine.fetch_integration_tools", AsyncMock(return_value=[]))


def _engine(script: Script) -> ConsciousEngine:
    """A fully wired engine: real gate, sessions, budget and repo prompts; mocked Redis, with
    the budget's counter on fakeredis (the mock cannot run its script)."""
    redis = _make_redis_mock()
    spend = build_spend_limits(
        fakeredis.FakeAsyncRedis(), cap_usd=5.0, clock=local_clock(lambda: "UTC")
    )
    return ConsciousEngine(
        deps=ConsciousDeps(
            redis=redis,
            identity_gate=IdentityGate(registered_phone="+1234567890"),
            session_mgr=SessionManager(redis=redis, timeout_minutes=30),
            spend=spend,
            domain_router=AsyncMock(),
            tool_registry=AsyncMock(get_tools=AsyncMock(return_value=[])),
            context_reader=AsyncMock(get_rendered_context=AsyncMock(return_value="")),
            agent=build_system2_agent(
                toolsets=system2_toolsets(None),
                max_tokens=256,
                timeout_s=5.0,
                spend=spend,
            ),
            model=script.model,
        )
    )


@pytest.mark.asyncio
async def test_full_pipeline_sir_gets_system_prompt() -> None:
    script = Script(_reply("Good morning, sir."))
    engine = _engine(script)
    with _no_integrations():
        response = await engine.process_request(_make_request(content="Good morning"))

    instructions = script.instructions()
    assert "sir (authenticated)" in instructions
    assert "opinionated" in instructions
    engine._redis.lpush.assert_called()  # type: ignore[attr-defined]
    assert response.text == "Good morning, sir."


@pytest.mark.asyncio
async def test_full_pipeline_guest_no_preferences() -> None:
    script = Script(_reply("Good evening."))
    with _no_integrations():
        response = await _engine(script).process_request(
            _make_request(identity_claim="guest", content="Hello", session_id="s2")
        )

    assert response.text == "Good evening."


@pytest.mark.asyncio
async def test_full_pipeline_session_history_forwarded() -> None:
    engine = _engine(Script(_reply("It is currently 3pm, sir.")))
    with _no_integrations():
        await engine.process_request(_make_request(content="What time is it?"))
    assert engine._redis.hset.call_count >= 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_full_pipeline_spend_counted() -> None:
    engine = _engine(Script(_reply("Hello, sir.")))
    with _no_integrations():
        await engine.process_request(_make_request(content="Hello"))
    (status,) = await engine._spend.status()
    assert status.spent.requests == 1


@pytest.mark.asyncio
async def test_full_pipeline_scratchpad_observation_format() -> None:
    engine = _engine(Script(_reply("Done, sir.")))
    with _no_integrations():
        await engine.process_request(_make_request(content="Turn on the lights"))

    observation: str = engine._redis.lpush.call_args.args[1]  # type: ignore[attr-defined]
    assert "[conscious]" in observation
    assert "Turn on the lights" in observation
    assert "tokens=100+20" in observation
