"""Tests for trigger engine HTTP server (JSON-RPC tool dispatch)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.dispatch = AsyncMock(return_value={"trigger_id": "t-1", "status": "created"})
    return client


@pytest.mark.asyncio
async def test_jsonrpc_dispatch(mock_client: AsyncMock) -> None:
    from core.triggers.server import handle_jsonrpc

    request: dict[str, Any] = {
        "method": "triggers.create_trigger",
        "params": {"name": "test", "trigger_type": "time", "conditions": {"cron": "0 7 * * *"}},
        "id": "req-1",
    }

    response = await handle_jsonrpc(request, mock_client)

    assert response["id"] == "req-1"
    assert "result" in response
    mock_client.dispatch.assert_called_once_with(
        "triggers.create_trigger",
        {"name": "test", "trigger_type": "time", "conditions": {"cron": "0 7 * * *"}},
    )


@pytest.mark.asyncio
async def test_jsonrpc_error(mock_client: AsyncMock) -> None:
    from core.triggers.server import handle_jsonrpc

    mock_client.dispatch = AsyncMock(side_effect=KeyError("Unknown tool: x"))

    request: dict[str, Any] = {
        "method": "x",
        "params": {},
        "id": "req-1",
    }

    response = await handle_jsonrpc(request, mock_client)
    assert "error" in response
