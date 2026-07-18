"""Shared fixtures for web channel tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from core.channels.web_server import create_app
from shared.streams import AUTH_SESSION_PREFIX

_TEST_SESSION_ID = "test-auth-session"
_AUTH_SESSION_DATA: dict[bytes, bytes] = {
    b"authenticated": b"1",
    b"credential_id": b"test-cred",
    b"created_at": b"2026-04-16T00:00:00",
}


@pytest.fixture
def web_client() -> TestClient:
    """Create a TestClient with a mocked Redis connection and auth session."""
    mock_redis = AsyncMock()

    async def _fake_hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_TEST_SESSION_ID}":
            return _AUTH_SESSION_DATA
        return {}

    mock_redis.hgetall = AsyncMock(side_effect=_fake_hgetall)
    mock_redis.hget = AsyncMock(return_value=None)

    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = mock_redis
    client = TestClient(app)
    client.cookies.set("alfred_auth", _TEST_SESSION_ID)
    return client


@pytest.fixture
def home_service_manifest() -> dict[str, object]:
    """A registry manifest for a sovereign service with credential support.

    Mirrors what AlfredClient.get_registration_manifest() writes to
    alfred:tool_registry for home-service (Plan 2 declares exactly this schema).
    """
    return {
        "service_name": "home-service",
        "service_endpoint": "http://localhost:8000/mcp",
        "features": [],
        "credentials_schema": {
            "fields": {
                "url": {
                    "label": "Home Assistant URL",
                    "field_type": "url",
                    "required": True,
                    "placeholder": "",
                    "default": "http://homeassistant.local:8123",
                    "help_text": "",
                    "transient": False,
                },
                "token": {
                    "label": "Access Token",
                    "field_type": "password",
                    "required": True,
                    "placeholder": "",
                    "default": "",
                    "help_text": "Long-lived access token from your HA profile page",
                    "transient": False,
                },
            }
        },
        "credentials_endpoint": "http://localhost:8000/credentials",
    }
