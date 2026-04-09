import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock.hset = AsyncMock()
    mock.hdel = AsyncMock()
    mock.hgetall = AsyncMock(return_value={})
    mock.close = AsyncMock()
    mock.xread = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def app(mock_redis):
    with patch("core.channels.web_server.aioredis") as mock_aioredis:
        mock_aioredis.from_url.return_value = mock_redis
        from core.channels.web_server import create_app

        app = create_app()
        app.state.redis = mock_redis
        yield app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_register_device_stores_token(client, mock_redis) -> None:
    resp = client.post(
        "/api/devices/register",
        json={
            "device_token": "abc123hex",
            "platform": "ios",
            "identity": "sir",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    mock_redis.hset.assert_called_once()
    call_args = mock_redis.hset.call_args
    assert call_args[0][0] == "alfred:push:devices"
    assert call_args[0][1] == "abc123hex"
    stored = json.loads(call_args[0][2])
    assert stored["platform"] == "ios"
    assert stored["identity"] == "sir"
    assert "registered_at" in stored


def test_unregister_device_removes_token(client, mock_redis) -> None:
    resp = client.request(
        "DELETE",
        "/api/devices/register",
        json={"device_token": "abc123hex"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    mock_redis.hdel.assert_called_once_with("alfred:push:devices", "abc123hex")


def test_register_device_missing_token_returns_422(client, mock_redis) -> None:
    resp = client.post(
        "/api/devices/register",
        json={"platform": "ios", "identity": "sir"},
    )
    assert resp.status_code == 422
