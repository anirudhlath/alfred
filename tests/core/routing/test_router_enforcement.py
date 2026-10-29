"""DomainRouter tiered-autonomy enforcement (contract C10)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from bus.schemas.events import ActionRequest, ActionResult, ParkedAction
from core.notifications.schema import Urgency

_MANIFEST = json.dumps(
    {
        "service_name": "home-service",
        "features": [
            {
                "name": "home",
                "tools": [
                    {"name": "home.turn_on_lights", "risk": "benign", "audience": "reflex"},
                    {"name": "home.set_climate", "risk": "elevated", "audience": "conscious"},
                    {"name": "home.unlock_door", "risk": "critical", "audience": "conscious"},
                ],
            }
        ],
    }
)


class StubAgent:
    def __init__(self) -> None:
        self.calls: list[ActionRequest] = []

    async def execute_action(self, action: ActionRequest) -> ActionResult:
        self.calls.append(action)
        return ActionResult(
            source="stub-agent",
            request_id=action.request_id,
            tool_name=action.tool_name,
            status="success",
            result={},
        )


def _router() -> tuple[object, StubAgent, AsyncMock, AsyncMock]:
    from core.routing.domain_router import DomainRouter

    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=_MANIFEST.encode())
    # redis-py's pipeline() is a sync call returning an async context manager (the call log's).
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    redis.pipeline = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=pipe)))
    notifier = AsyncMock()
    router = DomainRouter(redis=redis, notifier=notifier)
    agent = StubAgent()
    router.register("home-service", agent)
    return router, agent, redis, notifier


def _action(source: str, tool: str, confirmed: bool = False) -> ActionRequest:
    return ActionRequest(
        source=source,
        target_service="home-service",
        tool_name=tool,
        parameters={"room": "living_room"},
        confirmed=confirmed,
    )


@pytest.mark.asyncio
async def test_reflex_benign_dispatches() -> None:
    router, agent, _, _ = _router()
    result = await router.route(_action("reflex-engine", "home.turn_on_lights"))
    assert result.status == "success"
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_reflex_critical_rejected_not_intercepted() -> None:
    """Rule (a) wins for reflex sources — no pending entry, no notification."""
    router, _agent, redis, notifier = _router()
    result = await router.route(_action("reflex-engine", "home.unlock_door"))

    assert result.status == "error"
    assert result.error is not None and result.error.startswith("autonomy_violation")
    redis.set.assert_not_called()
    notifier.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_critical_unconfirmed_intercepted() -> None:
    router, agent, redis, notifier = _router()
    action = _action("conscious-engine", "home.unlock_door")
    result = await router.route(action)

    assert result.status == "error"
    assert result.error == f"confirmation_required:{action.request_id}"
    assert agent.calls == []
    # Pending entry stored with TTL 300, as a parked action
    args, kwargs = redis.set.call_args
    assert args[0] == f"alfred:pending:{action.request_id}"
    assert kwargs["ex"] == 300
    assert ParkedAction.model_validate_json(args[1]).action == action
    # URGENT notification with pending metadata
    notifier.publish.assert_awaited_once()
    pub_kwargs = notifier.publish.call_args.kwargs
    assert pub_kwargs["title"] == "Confirmation required"
    assert pub_kwargs["body"] == (
        "Alfred wants to run 'home.unlock_door' on home-service — confirm?"
    )
    assert pub_kwargs["source"] == "domain-router"
    assert pub_kwargs["urgency"] == Urgency.URGENT
    assert set(pub_kwargs["metadata"]) == {"pending_action_id", "tool_name", "parameters", "reason"}
    assert pub_kwargs["metadata"]["pending_action_id"] == action.request_id
    assert pub_kwargs["metadata"]["tool_name"] == "home.unlock_door"
    assert pub_kwargs["metadata"]["parameters"] == {"room": "living_room"}
    assert pub_kwargs["metadata"]["reason"] is None  # _action() sets no reason


@pytest.mark.asyncio
async def test_critical_intercept_forwards_reason_to_notification() -> None:
    router, _agent, _redis, notifier = _router()
    action = _action("conscious-engine", "home.unlock_door").model_copy(
        update={"reason": "The dog walker is at the door."}
    )
    await router.route(action)

    pub_kwargs = notifier.publish.call_args.kwargs
    assert pub_kwargs["metadata"]["reason"] == "The dog walker is at the door."


@pytest.mark.asyncio
async def test_critical_confirmed_dispatches_without_reinterception() -> None:
    router, agent, redis, notifier = _router()
    result = await router.route(_action("conscious-engine", "home.unlock_door", confirmed=True))

    assert result.status == "success"
    assert len(agent.calls) == 1
    redis.set.assert_not_called()
    notifier.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_redis_skips_enforcement() -> None:
    """DomainRouter() with no deps behaves exactly as before (backward compat)."""
    from core.routing.domain_router import DomainRouter

    router = DomainRouter()
    agent = StubAgent()
    router.register("home-service", agent)
    result = await router.route(_action("reflex-engine", "home.unlock_door"))
    assert result.status == "success"


@pytest.mark.asyncio
async def test_reflex_unregistered_tool_rejected() -> None:
    """A tool absent from the registry has unknown risk — reflex must not execute it.

    Regression: the Reflex SLM emitted `home.light_turn_on`, a name absent from the
    manifest. Unknown risk read as benign, so the autonomy gate passed it through and
    the house acted on an unconfirmed, unregistered action.
    """
    router, agent, redis, notifier = _router()
    result = await router.route(_action("reflex-engine", "home.light_turn_on"))

    assert result.status == "error"
    assert result.error is not None and result.error.startswith("autonomy_violation")
    assert agent.calls == []
    notifier.publish.assert_not_awaited()
    streams = [call.args[0] for call in redis.xadd.await_args_list]
    assert "alfred:reflex:observations" in streams


@pytest.mark.asyncio
async def test_conscious_unregistered_tool_still_dispatches() -> None:
    """Fail-closed applies to reflex autonomy only — System 2 keeps full action rights."""
    router, agent, redis, notifier = _router()
    result = await router.route(_action("conscious-engine", "home.light_turn_on"))

    assert result.status == "success"
    assert len(agent.calls) == 1
    redis.set.assert_not_called()
    notifier.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_reflex_elevated_confirmed_dispatches() -> None:
    """An elevated Reflex action the owner answered yes to runs (the Reflex ask path)."""
    router, agent, redis, notifier = _router()
    result = await router.route(_action("reflex-engine", "home.set_climate", confirmed=True))

    assert result.status == "success"
    assert len(agent.calls) == 1
    redis.set.assert_not_called()
    notifier.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "confirmed"),
    [
        ("home.set_climate", False),  # elevated, never answered
        ("home.unlock_door", True),  # critical: a yes does not let Reflex run it
        ("home.light_turn_on", True),  # unknown risk: a yes does not let Reflex run it
    ],
)
async def test_reflex_action_not_runnable_is_rejected(tool: str, confirmed: bool) -> None:
    router, agent, redis, notifier = _router()
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        result = await router.route(_action("reflex-engine", tool, confirmed=confirmed))
    finally:
        logger.remove(sink)

    assert result.status == "error"
    assert result.error is not None and result.error.startswith("autonomy_violation")
    assert agent.calls == []
    redis.set.assert_not_called()
    notifier.publish.assert_not_awaited()
    # The violation is recorded as a ReflexObservation, and logged with whether it was confirmed
    streams = [call.args[0] for call in redis.xadd.await_args_list]
    assert "alfred:reflex:observations" in streams
    assert any(f"confirmed={confirmed}" in line for line in warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "confirmed"),
    [
        ("home.set_climate", False),  # elevated, never answered
        ("home.unlock_door", True),  # critical, answered yes
    ],
)
async def test_conscious_action_ignores_reflex_rules(tool: str, confirmed: bool) -> None:
    """System 2 keeps its action rights: the Reflex rules do not apply to it."""
    router, agent, redis, notifier = _router()
    result = await router.route(_action("conscious-engine", tool, confirmed=confirmed))

    assert result.status == "success"
    assert len(agent.calls) == 1
    redis.set.assert_not_called()
    notifier.publish.assert_not_awaited()
