"""Tests for home domain sub-agent."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bus.schemas.events import ActionRequest


@pytest.mark.asyncio
async def test_home_agent_routes_dim_lights() -> None:
    from domains.home.home_agent import HomeAgent

    mock_redis = AsyncMock()
    mock_redis.hget = AsyncMock(
        return_value=json.dumps(
            {
                "service_name": "home-service",
                "service_endpoint": "http://home-service:8000/mcp",
                "tools": [{"name": "smart_home.dim_lights"}],
            }
        )
    )

    agent = HomeAgent(redis=mock_redis)

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "living_room", "level": 20},
    )

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = AsyncMock()
        mock_resp.json = MagicMock(return_value={"brightness": 20})
        mock_resp.raise_for_status = lambda: None
        mock_post.return_value = mock_resp

        result = await agent.execute_action(action)

    assert result.status == "success"
    assert result.tool_name == "smart_home.dim_lights"


@pytest.mark.asyncio
async def test_home_agent_handles_unknown_tool() -> None:
    from domains.home.home_agent import HomeAgent

    mock_redis = AsyncMock()
    mock_redis.hget = AsyncMock(return_value=None)

    agent = HomeAgent(redis=mock_redis)

    action = ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name="smart_home.unknown_tool",
        parameters={},
    )

    result = await agent.execute_action(action)
    assert result.status == "error"
    assert result.error is not None
    assert "not found" in result.error.lower()
