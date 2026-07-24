"""DomainRouter tiered-autonomy enforcement (contract C10)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ActionRequest, ActionResult
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
async def test_reflex_elevated_rejected_with_observation() -> None:
    router, agent, redis, _ = _router()
    result = await router.route(_action("reflex-engine", "home.set_climate"))

    assert result.status == "error"
    assert result.error is not None and result.error.startswith("autonomy_violation")
    assert agent.calls == []
    # A ReflexObservation was published to the observations stream
    streams = [call.args[0] for call in redis.xadd.await_args_list]
    assert "alfred:reflex:observations" in streams


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
    # Pending entry stored with TTL 300
    args, kwargs = redis.set.call_args
    assert args[0] == f"alfred:pending_actions:{action.request_id}"
    assert kwargs["ex"] == 300
    # URGENT notification with pending metadata
    notifier.publish.assert_awaited_once()
    pub_kwargs = notifier.publish.call_args.kwargs
    assert pub_kwargs["urgency"] == Urgency.URGENT
    assert pub_kwargs["metadata"]["pending_action_id"] == action.request_id
    assert pub_kwargs["metadata"]["tool_name"] == "home.unlock_door"
    assert pub_kwargs["metadata"]["parameters"] == {"room": "living_room"}


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
