"""Tests for DomainRouter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import fakeredis
import pytest
from redis import exceptions as redis_exceptions

from bus.schemas.events import ActionRequest, ActionResult
from core.attribution.log import AlfredCall
from core.routing.domain_router import CONFIRMATION_REQUIRED_PREFIX, DomainRouter
from shared.streams import ATTRIBUTION_CALLS_STREAM, TOOL_REGISTRY_KEY


class FakeAgent:
    """Fake domain agent for testing."""

    async def execute_action(self, action: ActionRequest) -> ActionResult:
        return ActionResult(
            source="fake-agent",
            request_id=action.request_id,
            tool_name=action.tool_name,
            status="success",
            result={"ok": True},
        )


class ContextAgent:
    """Fake domain agent whose service reports the context id of the change it made."""

    async def execute_action(self, action: ActionRequest) -> ActionResult:
        return ActionResult(
            source="fake-agent",
            request_id=action.request_id,
            tool_name=action.tool_name,
            status="success",
            result={"id": "r", "result": {"context_id": "abc"}},
        )


@pytest.fixture
def router() -> DomainRouter:
    r = DomainRouter()
    r.register("home-service", FakeAgent())
    return r


@pytest.mark.asyncio
async def test_route_to_registered_agent(router: DomainRouter) -> None:
    action = ActionRequest(
        source="test",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living", "level": 50},
    )
    result = await router.route(action)
    assert result.status == "success"
    assert result.tool_name == "smart_home.dim_lights"


@pytest.mark.asyncio
async def test_route_unknown_service_returns_error(router: DomainRouter) -> None:
    action = ActionRequest(
        source="test",
        target_service="unknown-service",
        tool_name="some.tool",
    )
    result = await router.route(action)
    assert result.status == "error"
    assert "unknown-service" in (result.error or "")


def test_register_multiple_agents() -> None:
    router = DomainRouter()
    router.register("svc-a", FakeAgent())
    router.register("svc-b", FakeAgent())
    assert router.registered_services == {"svc-a", "svc-b"}


def _call_action(source: str = "conscious-engine") -> ActionRequest:
    return ActionRequest(
        source=source,
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living", "level": 50},
    )


@pytest.mark.asyncio
async def test_route_records_a_successful_call() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router = DomainRouter(redis=redis)
    router.register("home-service", ContextAgent())
    action = _call_action()

    result = await router.route(action)

    assert result.status == "success"
    entries = await redis.xrange(ATTRIBUTION_CALLS_STREAM)
    assert len(entries) == 1
    call = AlfredCall.model_validate_json(entries[0][1][b"call"])
    assert call.source == action.source
    assert call.request_id == action.request_id
    assert call.context_id == "abc"


@pytest.mark.asyncio
async def test_route_records_no_call_it_did_not_execute() -> None:
    """An unregistered service and a rejected Reflex action never reached an agent."""
    redis = fakeredis.FakeAsyncRedis()
    router = DomainRouter(redis=redis)
    router.register("home-service", ContextAgent())

    unregistered = ActionRequest(source="conscious-engine", target_service="nope", tool_name="t")
    assert (await router.route(unregistered)).status == "error"
    assert (await router.route(_call_action("reflex-engine"))).status == "error"

    assert await redis.xlen(ATTRIBUTION_CALLS_STREAM) == 0


async def _critical_router(redis: fakeredis.FakeAsyncRedis) -> DomainRouter:
    """A router whose registry tags the tool critical."""
    manifest = {"features": [{"tools": [{"name": "smart_home.dim_lights", "risk": "critical"}]}]}
    await redis.hset(TOOL_REGISTRY_KEY, "home-service", json.dumps(manifest))
    router = DomainRouter(redis=redis)
    router.register("home-service", ContextAgent())
    return router


@pytest.mark.asyncio
async def test_route_records_a_critical_call_only_once_confirmed() -> None:
    """Parked for the owner's say-so, it was not executed; confirmed, it was."""
    redis = fakeredis.FakeAsyncRedis()
    router = await _critical_router(redis)

    parked = await router.route(_call_action())
    assert (parked.error or "").startswith(CONFIRMATION_REQUIRED_PREFIX)
    assert await redis.xlen(ATTRIBUTION_CALLS_STREAM) == 0

    confirmed = await router.route(_call_action().model_copy(update={"confirmed": True}))
    assert confirmed.status == "success"
    assert await redis.xlen(ATTRIBUTION_CALLS_STREAM) == 1


@pytest.mark.asyncio
async def test_route_returns_the_result_when_the_call_log_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = fakeredis.FakeAsyncRedis()
    refusal = redis_exceptions.ResponseError("OOM command not allowed when used memory")
    monkeypatch.setattr(redis, "pipeline", MagicMock(side_effect=refusal))
    router = DomainRouter(redis=redis)
    router.register("home-service", ContextAgent())

    result = await router.route(_call_action())

    assert result.status == "success"
    assert result.result == {"id": "r", "result": {"context_id": "abc"}}
