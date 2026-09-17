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

import pytest

from tests.core.channels.conftest import live_channels_client

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="web/dist not built")


@pytest.fixture
def spa_client() -> Iterator[TestClient]:
    """A real-lifespan TestClient serving the actual built `web/dist/`."""
    with live_channels_client() as client:
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
