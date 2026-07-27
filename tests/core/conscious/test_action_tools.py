"""Conscious internal action tools — confirmation + attention primitives."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ActionRequest, UserRequest
from core.identity.schemas import IdentityResult


@pytest.mark.asyncio
async def test_confirm_pending_action_tool_republishes() -> None:
    from core.conscious.action_tools import dispatch_action_tool

    action = ActionRequest(
        source="conscious-engine",
        target_service="home-service",
        tool_name="home.unlock_door",
    )
    redis = AsyncMock()
    redis.getdel = AsyncMock(return_value=action.model_dump_json().encode())

    result = json.loads(
        await dispatch_action_tool(
            "confirm_pending_action", {"request_id": action.request_id}, redis
        )
    )

    assert result["status"] == "confirmed"
    assert result["tool_name"] == "home.unlock_door"
    stream, fields = redis.xadd.call_args[0]
    assert stream == "alfred:actions"
    assert ActionRequest.model_validate_json(fields["event"]).confirmed is True


@pytest.mark.asyncio
async def test_confirm_pending_action_tool_expired() -> None:
    from core.conscious.action_tools import dispatch_action_tool

    redis = AsyncMock()
    redis.getdel = AsyncMock(return_value=None)
    result = json.loads(
        await dispatch_action_tool("confirm_pending_action", {"request_id": "x"}, redis)
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_attention_tools_roundtrip() -> None:
    from core.conscious.action_tools import dispatch_action_tool

    redis = AsyncMock()
    redis.smembers = AsyncMock(return_value={b"sensor.dryer_power"})

    added = json.loads(
        await dispatch_action_tool(
            "attention_add", {"domain": "home", "entity_id": "sensor.dryer_power"}, redis
        )
    )
    assert added["status"] == "added"
    redis.sadd.assert_any_call("alfred:attention:home", "sensor.dryer_power")

    listed = json.loads(await dispatch_action_tool("attention_list", {"domain": "home"}, redis))
    assert listed["entities"] == ["sensor.dryer_power"]

    removed = json.loads(
        await dispatch_action_tool(
            "attention_remove", {"domain": "home", "entity_id": "sensor.dryer_power"}, redis
        )
    )
    assert removed["status"] == "removed"
    redis.srem.assert_called_once_with("alfred:attention:home", "sensor.dryer_power")


@pytest.mark.asyncio
async def test_unknown_action_tool_returns_error() -> None:
    from core.conscious.action_tools import dispatch_action_tool

    result = json.loads(await dispatch_action_tool("attention_explode", {}, AsyncMock()))
    assert "error" in result


def _make_request(**overrides: object) -> UserRequest:
    defaults: dict[str, object] = {
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


def _make_engine(identity: IdentityResult) -> object:
    from core.conscious.engine import ConsciousEngine

    identity_gate = MagicMock()
    identity_gate.resolve.return_value = identity
    session_mgr = AsyncMock()
    session_mgr.get_or_create.return_value = {"channel": "web_pwa", "history": []}
    cost_tracker = AsyncMock()
    cost_tracker.is_budget_exceeded.return_value = False
    context_assembler = MagicMock()
    context_assembler.assemble.return_value = "You are Alfred."
    tool_registry = AsyncMock()
    tool_registry.get_tools.return_value = []

    return ConsciousEngine(
        redis=AsyncMock(),
        identity_gate=identity_gate,
        session_mgr=session_mgr,
        cost_tracker=cost_tracker,
        context_assembler=context_assembler,
        domain_router=AsyncMock(),
        tool_registry=tool_registry,
        context_reader=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_sir_identity_gets_action_tools_manifest() -> None:
    """The verified user ('sir') sees confirm_pending_action + attention_* tools."""
    from unittest.mock import patch

    from core.conscious.action_tools import ACTION_TOOLS_MANIFEST

    engine = _make_engine(
        IdentityResult(
            identity="sir",
            confidence=0.99,
            method="webauthn",
            factors=["webauthn"],
            risk_clearance="high",
        )
    )

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Good evening, sir.", [], 100, 50)
        await engine.process_request(_make_request())

    openai_tools = mock_llm.call_args[0][2]
    tool_names = {t["function"]["name"] for t in openai_tools}
    for manifest_tool in ACTION_TOOLS_MANIFEST:
        assert manifest_tool["function"]["name"] in tool_names


@pytest.mark.asyncio
async def test_non_sir_identity_does_not_get_action_tools_manifest() -> None:
    """A guest/unverified speaker must NOT be offered confirm/attention tools —
    this is the security gate: a guest must not be able to confirm a lock actuation."""
    from unittest.mock import patch

    from core.conscious.action_tools import ACTION_TOOL_NAMES

    engine = _make_engine(
        IdentityResult(
            identity="guest",
            confidence=0.4,
            method="unclaimed",
            factors=[],
            risk_clearance="low",
        )
    )

    with patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = ("Hello.", [], 100, 50)
        await engine.process_request(_make_request(identity_claim="guest", authenticated=False))

    openai_tools = mock_llm.call_args[0][2]
    tool_names = {t["function"]["name"] for t in openai_tools}
    assert tool_names.isdisjoint(ACTION_TOOL_NAMES)


@pytest.mark.asyncio
async def test_guest_hallucinated_action_tool_call_refused_end_to_end() -> None:
    """Full pipeline defense-in-depth: the manifest gate already keeps action tools
    out of a guest turn's tool list, but if the model hallucinates the call anyway
    (e.g. from prior conversation context), the dispatch-side identity gate must
    still refuse it — the pending-action store is never touched."""
    from unittest.mock import patch

    engine = _make_engine(
        IdentityResult(
            identity="guest",
            confidence=0.4,
            method="unclaimed",
            factors=[],
            risk_clearance="low",
        )
    )
    hallucinated_call = [
        {"id": "tc-1", "name": "confirm_pending_action", "input": {"request_id": "req-1"}}
    ]

    with (
        patch.object(engine, "_call_llm", new_callable=AsyncMock) as mock_llm,
        patch(
            "core.conscious.engine.dispatch_action_tool", new_callable=AsyncMock
        ) as mock_dispatch,
    ):
        mock_llm.side_effect = [
            ("", hallucinated_call, 100, 50),
            ("I can't do that for you.", [], 50, 20),
        ]
        response = await engine.process_request(
            _make_request(identity_claim="guest", authenticated=False)
        )

    mock_dispatch.assert_not_called()
    assert response.actions_taken == ["confirm_pending_action"]


@pytest.mark.asyncio
async def test_engine_dispatches_action_tool() -> None:
    """The engine routes ACTION_TOOL_NAMES through dispatch_action_tool in-process."""
    from core.conscious.engine import ConsciousEngine

    redis = AsyncMock()
    redis.smembers = AsyncMock(return_value=set())
    engine = ConsciousEngine(
        redis=redis,
        identity_gate=MagicMock(),
        session_mgr=AsyncMock(),
        cost_tracker=AsyncMock(),
        context_assembler=MagicMock(),
        domain_router=AsyncMock(),
        tool_registry=AsyncMock(),
        context_reader=AsyncMock(),
    )

    result = await engine._dispatch_tool_call(
        {"id": "t1", "name": "attention_list", "input": {"domain": "home"}}, tools=[]
    )

    assert result["tool_use_id"] == "t1"
    assert json.loads(result["content"])["entities"] == []
