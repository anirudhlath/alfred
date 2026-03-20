"""Tests for DomainRouter."""

from __future__ import annotations

import pytest

from bus.schemas.events import ActionRequest, ActionResult
from core.routing.domain_router import DomainRouter


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
