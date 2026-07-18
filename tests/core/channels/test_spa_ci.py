"""CI guard: with web/dist built, the SPA catch-all must serve / but never shadow /api/auth/*.

The auth router and the SPA catch-all are only mounted inside `create_app`'s FastAPI
lifespan (see `core/channels/web_server.py::_lifespan`), so this test must drive the
app as a context manager (`with TestClient(app) as client:`) to actually exercise
route-registration order. `web/dist/` never existed in CI before, so this exact
route-shadowing regression was invisible (see CLAUDE.md gotcha on `mount_spa`).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.channels.web_server import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="web/dist not built")


@pytest.fixture
def spa_client() -> Iterator[TestClient]:
    """A real-lifespan TestClient serving the actual built `web/dist/`."""
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

    with (
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
        patch("core.channels.web_server.start_warmup", return_value=MagicMock()),
        # httpx.AsyncClient.aclose() is called on shutdown.
        patch("httpx.AsyncClient.aclose", new=AsyncMock()),
    ):
        app = create_app(redis_url="redis://localhost:6379")
        with TestClient(app) as client:
            yield client


def test_spa_index_served(spa_client: TestClient) -> None:
    r = spa_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_auth_routes_not_shadowed_by_spa(spa_client: TestClient) -> None:
    r = spa_client.post("/api/auth/login/begin")
    # Any JSON API response (even 4xx) proves the auth router won; HTML means the
    # SPA catch-all shadowed it.
    assert "text/html" not in r.headers.get("content-type", "")
