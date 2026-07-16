"""Tests for the merged integrations API — registry-declared sovereign services.

Contract C5: adapters (IntegrationRegistry) and sovereign services
(alfred:tool_registry manifests with a credentials_schema) share the same
/api/integrations surface; service entries are marked kind="service".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

from core.channels.web_server import create_app
from core.integrations.base import (
    CredentialField,
    CredentialSchema,
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry
from shared.streams import AUTH_SESSION_PREFIX, TOOL_REGISTRY_KEY

_TEST_SESSION_ID = "test-auth-session"
_AUTH_SESSION_DATA: dict[bytes, bytes] = {
    b"authenticated": b"1",
    b"credential_id": b"test-cred",
    b"created_at": b"2026-04-16T00:00:00",
}


class _KindAdapter(Integration):
    """Minimal in-process adapter to verify kind='adapter' marking."""

    name = "kind_adapter"
    category = "testing"
    credentials_schema = CredentialSchema(fields={"key": CredentialField(label="Key")})

    def __init__(self, key: str = "") -> None:
        self.key = key

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return []

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={}, freshness=datetime.now(UTC), confidence=0.0)

    async def health_check(self) -> bool:
        return True


class _ServiceHttpHandler:
    """Programmable fake sovereign service for httpx.MockTransport."""

    def __init__(self) -> None:
        self.pushes: list[dict[str, str]] = []
        self.push_fails = False
        self.unreachable = False
        self.health: dict[str, Any] = {
            "status": "ok",
            "service": "home-service",
            "ha": {"state": "connected", "entities": 87, "areas": 6, "last_event_age_s": 2.1},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("connection refused")
        if request.url.path == "/credentials":
            if self.push_fails:
                return httpx.Response(500, json={"detail": "boom"})
            self.pushes.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "ok", "health": self.health})
        if request.url.path == "/health":
            return httpx.Response(200, json=self.health)
        return httpx.Response(404)


@pytest.fixture
def service_handler() -> _ServiceHttpHandler:
    return _ServiceHttpHandler()


def _build_service_client(
    manifest: dict[str, Any], service_handler: _ServiceHttpHandler
) -> Iterator[TestClient]:
    """Shared TestClient builder: home-service manifest in a mocked tool registry
    + fake service HTTP. Factored out so variant manifests (e.g. missing
    credentials_endpoint) can reuse the same hermetic registry snapshot/restore.
    """
    registry_data = {b"home-service": json.dumps(manifest).encode()}

    mock_redis = AsyncMock()

    async def _fake_hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_TEST_SESSION_ID}":
            return _AUTH_SESSION_DATA
        if key == TOOL_REGISTRY_KEY:
            return registry_data
        return {}

    async def _fake_hget(key: str, field: str) -> bytes | None:
        if key == TOOL_REGISTRY_KEY:
            return registry_data.get(field.encode())
        return None

    mock_redis.hgetall = AsyncMock(side_effect=_fake_hgetall)
    mock_redis.hget = AsyncMock(side_effect=_fake_hget)

    app = create_app(redis_url="redis://localhost:6379")
    # create_app imports the real adapter modules (decorators register once per
    # session) — clear AFTER app creation so only test-controlled entries exist.
    # Snapshot the real registry state and restore it on teardown so this
    # fixture stays hermetic under any test ordering — the @register
    # decorators on real adapters only run once per session, so a later test
    # relying on the un-mocked registry must see it intact.
    registry_snapshot = dict(IntegrationRegistry._registry)
    instances_snapshot = dict(IntegrationRegistry._instances)
    IntegrationRegistry._registry.clear()
    IntegrationRegistry._instances.clear()
    IntegrationRegistry._registry["kind_adapter"] = _KindAdapter

    app.state.redis = mock_redis
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(service_handler))
    client = TestClient(app)
    client.cookies.set("alfred_auth", _TEST_SESSION_ID)
    try:
        yield client
    finally:
        IntegrationRegistry._registry.clear()
        IntegrationRegistry._registry.update(registry_snapshot)
        IntegrationRegistry._instances.clear()
        IntegrationRegistry._instances.update(instances_snapshot)


@pytest.fixture
def service_client(
    service_handler: _ServiceHttpHandler, home_service_manifest: dict[str, Any]
) -> Iterator[TestClient]:
    """TestClient with home-service in a mocked tool registry + fake service HTTP."""
    yield from _build_service_client(home_service_manifest, service_handler)


@pytest.fixture
def home_service_manifest_no_endpoint(
    home_service_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Manifest variant with a credentials_schema but no credentials_endpoint —
    exercises the pushed:false branch of the PUT handler."""
    manifest = dict(home_service_manifest)
    manifest.pop("credentials_endpoint", None)
    return manifest


@pytest.fixture
def service_client_no_endpoint(
    service_handler: _ServiceHttpHandler, home_service_manifest_no_endpoint: dict[str, Any]
) -> Iterator[TestClient]:
    """TestClient for a home-service manifest with no credentials_endpoint."""
    yield from _build_service_client(home_service_manifest_no_endpoint, service_handler)


# ── GET (merged listing) ──


def test_get_lists_service_entry(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations")
    assert resp.status_code == 200
    svc = next(e for e in resp.json() if e["name"] == "home-service")
    assert svc["kind"] == "service"
    assert svc["category"] == "service"
    assert set(svc["schema"]["fields"]) == {"url", "token"}
    assert svc["configured"] == {"url": False, "token": False}


def test_get_marks_adapters_with_kind(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations")
    adapter = next(e for e in resp.json() if e["name"] == "kind_adapter")
    assert adapter["kind"] == "adapter"
    assert adapter["category"] == "testing"


def test_get_service_configured_after_secret_stored(service_client: TestClient) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    resp = service_client.get("/api/integrations")
    svc = next(e for e in resp.json() if e["name"] == "home-service")
    assert svc["configured"] == {"url": True, "token": False}


def test_get_never_returns_secret_values(service_client: TestClient) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "token", "super_secret_ha_token")
    resp = service_client.get("/api/integrations")
    assert "super_secret_ha_token" not in resp.text


# ── PUT (store + push) ──


def test_put_stores_and_pushes(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://192.168.50.159:8123", "token": "abc123"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "pushed": True}

    from shared.secrets import get_secret

    assert get_secret("home-service", "url") == "http://192.168.50.159:8123"
    assert get_secret("home-service", "token") == "abc123"
    assert service_handler.pushes == [{"url": "http://192.168.50.159:8123", "token": "abc123"}]


def test_put_unknown_field_422(service_client: TestClient) -> None:
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://x", "token": "t", "bogus": "v"},
    )
    assert resp.status_code == 422


def test_put_missing_required_422(service_client: TestClient) -> None:
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://x"},
    )
    assert resp.status_code == 422


def test_put_unreachable_service_502_keyring_persists(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    service_handler.unreachable = True
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://192.168.50.159:8123", "token": "abc123"},
    )
    assert resp.status_code == 502

    from shared.secrets import get_secret

    # Keyring write persisted — the worker re-pushes on the next ServiceRegistered.
    assert get_secret("home-service", "token") == "abc123"


def test_put_service_error_response_502_keyring_persists(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    """Service reachable but returns a 5xx on /credentials — same 502 mapping
    as an unreachable service, exercised via the HTTP-error status path rather
    than a connection failure."""
    service_handler.push_fails = True
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://192.168.50.159:8123", "token": "abc123"},
    )
    assert resp.status_code == 502

    from shared.secrets import get_secret

    # Keyring write persisted — the worker re-pushes on the next ServiceRegistered.
    assert get_secret("home-service", "token") == "abc123"


def test_put_no_credentials_endpoint_returns_pushed_false(
    service_client_no_endpoint: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    """A manifest with a credentials_schema but no credentials_endpoint stores
    to keyring and reports pushed=False without attempting an HTTP push."""
    resp = service_client_no_endpoint.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://192.168.50.159:8123", "token": "abc123"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "pushed": False}
    assert service_handler.pushes == []

    from shared.secrets import get_secret

    assert get_secret("home-service", "url") == "http://192.168.50.159:8123"
    assert get_secret("home-service", "token") == "abc123"


def test_put_unknown_name_404(service_client: TestClient) -> None:
    resp = service_client.put("/api/integrations/nonexistent/credentials", json={"x": "y"})
    assert resp.status_code == 404


# ── DELETE ──


def test_delete_service_credentials(service_client: TestClient) -> None:
    from shared.secrets import get_secret, set_secret

    set_secret("home-service", "url", "http://old")
    set_secret("home-service", "token", "old")
    resp = service_client.delete("/api/integrations/home-service/credentials")
    assert resp.status_code == 200
    assert get_secret("home-service", "url") is None
    assert get_secret("home-service", "token") is None


def test_delete_unknown_name_404(service_client: TestClient) -> None:
    resp = service_client.delete("/api/integrations/nonexistent/credentials")
    assert resp.status_code == 404


# ── status proxy ──


def test_status_proxies_health_connected(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations/home-service/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "home-service"
    assert data["healthy"] is True
    assert data["detail"]["ha"]["state"] == "connected"


def test_status_unhealthy_on_auth_failed(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    service_handler.health["ha"] = {
        "state": "auth_failed",
        "entities": 0,
        "areas": 0,
        "last_event_age_s": None,
    }
    resp = service_client.get("/api/integrations/home-service/status")
    data = resp.json()
    assert data["healthy"] is False
    assert data["detail"]["ha"]["state"] == "auth_failed"


def test_status_unreachable_service(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    service_handler.unreachable = True
    resp = service_client.get("/api/integrations/home-service/status")
    data = resp.json()
    assert data["healthy"] is False
    assert "error" in data["detail"]


def test_status_unknown_name_404(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations/nonexistent/status")
    assert resp.status_code == 404
