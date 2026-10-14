"""Tests for home domain sub-agent."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bus.schemas.events import ActionRequest
from domains.home.home_agent import HomeAgent


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


def _argument_error_message() -> str:
    from sdk.alfred_sdk.feature import ToolArgumentError

    error = ToolArgumentError(
        "smart_home.dim_lights", [("level", "Field required"), ("colour", "Unexpected argument")]
    )
    return str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service_error",
    [
        # home-service's /mcp: the exception's type and message, as one string.
        pytest.param(f"ToolArgumentError: {_argument_error_message()}", id="string"),
        # Any JSON-RPC 2.0 server that returns an error object.
        pytest.param({"code": -32602, "message": _argument_error_message()}, id="object"),
    ],
)
async def test_home_agent_hands_back_a_refused_arguments_message(service_error: object) -> None:
    """What System 2 is shown for arguments the SDK refused: the tool and every field."""
    from domains.home.home_agent import HomeAgent

    mock_redis = AsyncMock()
    mock_redis.hget = AsyncMock(
        return_value=json.dumps({"service_endpoint": "http://home-service:8000/mcp"})
    )
    action = ActionRequest(
        source="conscious-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"colour": "red"},
    )

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = AsyncMock()
        mock_resp.json = MagicMock(return_value={"id": action.request_id, "error": service_error})
        mock_resp.raise_for_status = lambda: None
        mock_post.return_value = mock_resp
        result = await HomeAgent(redis=mock_redis).execute_action(action)

    assert result.status == "error"
    assert result.error is not None
    assert _argument_error_message() in result.error
    assert "Traceback" not in result.error


def _retry_agent_and_action() -> tuple[HomeAgent, ActionRequest, list[float]]:
    from domains.home.home_agent import HomeAgent

    mock_redis = AsyncMock()
    mock_redis.hget = AsyncMock(
        return_value=json.dumps({"service_endpoint": "http://home-service:8000/mcp"})
    )
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    action = ActionRequest(
        source="conscious-engine",
        target_service="home-service",
        tool_name="smart_home.dim_lights",
        parameters={"room": "kitchen"},
    )
    return HomeAgent(redis=mock_redis, sleep=fake_sleep), action, slept


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.json = MagicMock(return_value={"brightness": 20})
    resp.raise_for_status = lambda: None
    return resp


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "never_left",
    [
        pytest.param(httpx.ConnectError("refused"), id="connect-error"),
        pytest.param(httpx.ConnectTimeout("slow connect"), id="connect-timeout"),
        pytest.param(httpx.PoolTimeout("no free connection"), id="pool-timeout"),
    ],
)
async def test_home_agent_retries_a_request_that_never_left(never_left: Exception) -> None:
    agent, action, slept = _retry_agent_and_action()

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.side_effect = [never_left, never_left, _ok_response()]
        result = await agent.execute_action(action)

    assert result.status == "success"
    assert mock_post.call_count == 3
    assert len(slept) == 2
    # Full jitter: attempt n waits uniform(0, min(2.0, 0.2 * 2 ** (n - 1))).
    assert 0 <= slept[0] <= 0.2
    assert 0 <= slept[1] <= 0.4


@pytest.mark.asyncio
async def test_home_agent_gives_up_after_three_connect_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent, action, slept = _retry_agent_and_action()

    with (
        caplog.at_level(logging.WARNING, logger="domains.home.home_agent"),
        patch("httpx.AsyncClient.post") as mock_post,
    ):
        mock_post.side_effect = httpx.ConnectError("refused")
        result = await agent.execute_action(action)

    assert result.status == "error"
    assert result.error is not None
    assert "ConnectError" in result.error
    assert mock_post.call_count == 3
    assert len(slept) == 2
    # One WARNING per retry, none for the final attempt.
    retries = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(retries) == 2


@pytest.mark.asyncio
async def test_home_agent_does_not_retry_a_read_timeout() -> None:
    """The request may have reached the service, so acting again could act twice."""
    agent, action, slept = _retry_agent_and_action()

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.side_effect = httpx.ReadTimeout("no answer")
        result = await agent.execute_action(action)

    assert result.status == "error"
    assert result.error is not None
    assert "ReadTimeout" in result.error
    assert mock_post.call_count == 1
    assert slept == []


@pytest.mark.asyncio
async def test_home_agent_does_not_retry_a_status_error() -> None:
    agent, action, slept = _retry_agent_and_action()

    resp = MagicMock()
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("POST", "http://home-service:8000/mcp"),
            response=httpx.Response(500),
        )
    )
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = resp
        result = await agent.execute_action(action)

    assert result.status == "error"
    assert mock_post.call_count == 1
    assert slept == []
