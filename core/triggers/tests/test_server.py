"""Tests for trigger engine FastAPI server (REST + JSON-RPC shim)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


@pytest.fixture
def mock_feature() -> AsyncMock:
    feature = AsyncMock()
    feature.create_trigger = AsyncMock(return_value={"trigger_id": "t-1", "name": "test"})
    feature.list_triggers = AsyncMock(return_value=[])
    feature.update_trigger = AsyncMock(return_value={"trigger_id": "t-1", "name": "updated"})
    feature.delete_trigger = AsyncMock(return_value={"status": "deleted", "trigger_id": "t-1"})
    feature.toggle_trigger = AsyncMock(return_value={"trigger_id": "t-1", "enabled": False})
    return feature


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.dispatch = AsyncMock(return_value={"trigger_id": "t-1", "status": "created"})
    return client


@pytest.fixture
def test_client(mock_feature: AsyncMock, mock_client: AsyncMock) -> TestClient:
    from core.triggers.server import create_app

    app = create_app(client=mock_client, feature=mock_feature)
    return TestClient(app)


def test_health_check(test_client: TestClient) -> None:
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_trigger(test_client: TestClient, mock_feature: AsyncMock) -> None:
    payload: dict[str, Any] = {
        "name": "test",
        "trigger_type": "time",
        "conditions": {"cron": "0 7 * * *"},
    }
    response = test_client.post("/triggers", json=payload)
    assert response.status_code == 200
    mock_feature.create_trigger.assert_called_once()


def test_list_triggers(test_client: TestClient, mock_feature: AsyncMock) -> None:
    response = test_client.get("/triggers")
    assert response.status_code == 200
    mock_feature.list_triggers.assert_called_once()


def test_update_trigger(test_client: TestClient, mock_feature: AsyncMock) -> None:
    payload: dict[str, Any] = {"name": "updated"}
    response = test_client.patch("/triggers/t-1", json=payload)
    assert response.status_code == 200
    mock_feature.update_trigger.assert_called_once()


def test_delete_trigger(test_client: TestClient, mock_feature: AsyncMock) -> None:
    response = test_client.delete("/triggers/t-1")
    assert response.status_code == 200
    mock_feature.delete_trigger.assert_called_once()


def test_toggle_trigger(test_client: TestClient, mock_feature: AsyncMock) -> None:
    response = test_client.patch("/triggers/t-1/toggle", json={"enabled": False})
    assert response.status_code == 200
    mock_feature.toggle_trigger.assert_called_once()


def test_jsonrpc_shim(test_client: TestClient, mock_client: AsyncMock) -> None:
    """JSON-RPC backward-compat shim delegates to AlfredClient.dispatch()."""
    rpc_request: dict[str, Any] = {
        "method": "triggers.create_trigger",
        "params": {"name": "test", "trigger_type": "time", "conditions": {"cron": "0 7 * * *"}},
        "id": "req-1",
    }
    response = test_client.post("/jsonrpc", json=rpc_request)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "req-1"
    assert "result" in data
    mock_client.dispatch.assert_called_once()


def test_jsonrpc_shim_error(test_client: TestClient, mock_client: AsyncMock) -> None:
    """JSON-RPC shim returns error when dispatch raises."""
    mock_client.dispatch = AsyncMock(side_effect=KeyError("unknown"))
    rpc_request: dict[str, Any] = {"method": "bad", "params": {}, "id": "req-1"}
    response = test_client.post("/jsonrpc", json=rpc_request)
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
