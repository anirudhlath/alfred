"""Shared fixtures for web channel tests."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import core.channels.web_server as ws_mod
from core.channels.web_server import create_app
from shared.streams import AUTH_SESSION_PREFIX

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_TEST_SESSION_ID = "test-auth-session"
_AUTH_SESSION_DATA: dict[bytes, bytes] = {
    b"authenticated": b"1",
    b"credential_id": b"test-cred",
    b"created_at": b"2026-04-16T00:00:00",
}


def session_hgetall(session_id: str = _TEST_SESSION_ID) -> AsyncMock:
    """The HGETALL half of `make_session_redis`, on its own.

    For tests that build their own Redis fake: assign it (`r.hgetall = session_hgetall()`)
    when the session is all HGETALL has to serve, or `await` it from a composite side
    effect and fall through on the empty result when it also has to serve test data.
    """

    async def _fake_hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{session_id}":
            return _AUTH_SESSION_DATA
        return {}

    return AsyncMock(side_effect=_fake_hgetall)


def make_session_redis(session_id: str = _TEST_SESSION_ID) -> AsyncMock:
    """A Redis fake that recognises exactly one live auth session.

    AuthCookieMiddleware reads `{AUTH_SESSION_PREFIX}{cookie}` and treats
    `authenticated == "1"` as signed in, so every "signed-in client" test wants this
    same HGETALL side effect plus the usual no-op writers. Callers that need more
    (a tool registry, a ctx hash, a stream) override that one method on the mock and
    delegate the miss back to this one, rather than restating the session branch.
    """
    mock = AsyncMock()
    mock.hgetall = session_hgetall(session_id)
    mock.hget = AsyncMock(return_value=None)
    mock.hset = AsyncMock()
    mock.hdel = AsyncMock()
    mock.close = AsyncMock()
    mock.xread = AsyncMock(return_value=[])
    return mock


@contextmanager
def live_channels_client(dist: Path | None = None) -> Iterator[TestClient]:
    """A TestClient over the real `create_app`, with every external dependency mocked.

    The lifespan has to actually run — it is what registers the auth router ahead of the
    SPA catch-all — so everything it reaches for is patched out here. Pass `dist` to point
    `_SPA_DIST` at a fixture tree; leave it None to serve the real `web/dist/`.
    """
    # Minimal mock Redis — handles auth-session lookup and any other calls.
    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value={})
    mock_redis.close = AsyncMock()

    # Minimal mock CredentialStore — initialize/close are no-ops; reports no credentials.
    mock_store = AsyncMock()
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.get_user_id = AsyncMock(return_value=None)
    mock_store.list_credentials = AsyncMock(return_value=[])
    mock_store.has_any_credential = AsyncMock(return_value=False)

    patches = [
        # Prevent aioredis.from_url from connecting to a real Redis.
        patch("core.channels.web_server.aioredis.from_url", return_value=mock_redis),
        # Skip the real CredentialStore (writes to data/credentials.db).
        patch("core.channels.web_server.CredentialStore", return_value=mock_store),
        # Skip APNs adapter init (needs .p8 key on disk).
        patch("core.channels.web_server._init_apns_adapter", new=AsyncMock()),
        # Skip the notification delivery worker background task (imported inside lifespan).
        patch(
            "core.notifications.delivery.notification_delivery_worker",
            new=AsyncMock(return_value=None),
        ),
        # Skip the credential push worker — the real one busy-spins against the
        # AsyncMock redis (xreadgroup returns instantly, never suspends), starving
        # the lifespan event loop so requests never complete.
        patch(
            "core.channels.service_credentials.credential_push_worker",
            new=AsyncMock(return_value=None),
        ),
        # Skip warmup — real Whisper/Piper loads in to_thread outlive the TestClient.
        # None, not a MagicMock: a mock never fires add_done_callback, so asyncio.wait
        # in teardown burns its full timeout. teardown skips None tasks.
        patch("core.channels.web_server.start_warmup", return_value=None),
        # httpx.AsyncClient.aclose() is called on shutdown.
        patch("httpx.AsyncClient.aclose", new=AsyncMock()),
    ]
    if dist is not None:
        # Point _SPA_DIST at the fixture dist so mount_spa actually mounts.
        patches.append(patch.object(ws_mod, "_SPA_DIST", dist))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        app = create_app(redis_url="redis://localhost:6379")
        with TestClient(app) as client:
            yield client


@pytest.fixture
def web_client() -> TestClient:
    """Create a TestClient with a mocked Redis connection and auth session."""
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = make_session_redis()
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
