"""Tests for the merged integrations API — registry-declared sovereign services.

Contract C5: adapters (IntegrationRegistry) and sovereign services
(alfred:tool_registry manifests with a credentials_schema) share the same
/api/integrations surface; service entries are marked kind="service".
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

from core.channels import web_server
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
from shared.streams import TOOL_REGISTRY_KEY
from tests.core.channels.conftest import _TEST_SESSION_ID, make_session_redis


class _FakeClock:
    """Deterministic stand-in for the ``time`` module — advances only when told.

    Substituted for ``web_server.time`` so a test can charge a known cost to the
    manifest lookup and a different one to the probe, making both the *scope* and
    the *precision* of ``latency_ms`` exactly assertable.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def perf_counter(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# The work that must NOT be timed (the manifest lookup, adapter construction) is
# charged an absurd cost and the probe a small one with a second decimal, so a
# timer that starts too early or rounds too finely can't produce
# _EXPECTED_PROBE_MS.
_LOOKUP_SECONDS = 500.0
_CONSTRUCT_SECONDS = 500.0
_PROBE_SECONDS = 0.0021239
_EXPECTED_PROBE_MS = 2.1


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


class _TimedAdapter(Integration):
    """Adapter charging fake time to construction and to health_check separately.

    `IntegrationRegistry.get` constructs lazily and blocks on a keyring read, so
    this pins that latency_ms bills the probe and not that cold start.
    """

    name = "timed_adapter"
    category = "testing"
    credentials_schema = CredentialSchema(fields={})
    clock: _FakeClock | None = None

    def __init__(self) -> None:
        if _TimedAdapter.clock is not None:
            _TimedAdapter.clock.advance(_CONSTRUCT_SECONDS)

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return []

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={}, freshness=datetime.now(UTC), confidence=0.0)

    async def health_check(self) -> bool:
        if _TimedAdapter.clock is not None:
            _TimedAdapter.clock.advance(_PROBE_SECONDS)
        return True


class _ServiceHttpHandler:
    """Programmable fake sovereign service for httpx.MockTransport."""

    def __init__(self) -> None:
        self.pushes: list[dict[str, str]] = []
        self.push_fails = False
        self.unreachable = False
        # Fired on every request — lets a test charge fake time to the probe.
        self.on_request: Callable[[], None] | None = None
        # Raised instead of responding — for the unexpected-error probe path.
        self.crash: Exception | None = None
        self.health: dict[str, Any] = {
            "status": "ok",
            "service": "home-service",
            "ha": {"state": "connected", "entities": 87, "areas": 6, "last_event_age_s": 2.1},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.on_request is not None:
            self.on_request()
        if self.crash is not None:
            raise self.crash
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
    manifest: dict[str, Any],
    service_handler: _ServiceHttpHandler,
    *,
    signed_in: bool = True,
    on_manifest_read: Callable[[], None] | None = None,
) -> Iterator[TestClient]:
    """Shared TestClient builder: home-service manifest in a mocked tool registry
    + fake service HTTP. Factored out so variant manifests (e.g. missing
    credentials_endpoint) can reuse the same hermetic registry snapshot/restore.
    """
    registry_data = {b"home-service": json.dumps(manifest).encode()}

    # The session half of the fake is shared (conftest); overlay the tool registry
    # and delegate every other key back to it.
    mock_redis = make_session_redis()
    session_hgetall = mock_redis.hgetall

    async def _fake_hgetall(key: str) -> dict[bytes, bytes]:
        if key == TOOL_REGISTRY_KEY:
            return registry_data
        result: dict[bytes, bytes] = await session_hgetall(key)
        return result

    async def _fake_hget(key: str, field: str) -> bytes | None:
        if key == TOOL_REGISTRY_KEY:
            if on_manifest_read is not None:
                on_manifest_read()
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
    if signed_in:
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
def anon_service_client(
    service_handler: _ServiceHttpHandler, home_service_manifest: dict[str, Any]
) -> Iterator[TestClient]:
    """Same wiring as `service_client`, but never signed in — for the 401 paths."""
    yield from _build_service_client(home_service_manifest, service_handler, signed_in=False)


@pytest.fixture
def home_service_manifest_no_credentials_endpoint(
    home_service_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Manifest variant with a credentials_schema but no credentials_endpoint —
    exercises the pushed:false branch of the PUT handler."""
    manifest = dict(home_service_manifest)
    manifest.pop("credentials_endpoint", None)
    return manifest


@pytest.fixture
def service_client_no_credentials_endpoint(
    service_handler: _ServiceHttpHandler,
    home_service_manifest_no_credentials_endpoint: dict[str, Any],
) -> Iterator[TestClient]:
    """TestClient for a home-service manifest with no credentials_endpoint."""
    yield from _build_service_client(home_service_manifest_no_credentials_endpoint, service_handler)


@pytest.fixture
def home_service_manifest_without_any_endpoint(
    home_service_manifest_no_credentials_endpoint: dict[str, Any],
) -> dict[str, Any]:
    """Manifest variant declaring *neither* endpoint — distinct from
    `home_service_manifest_no_credentials_endpoint`, which still has a service_endpoint."""
    manifest = dict(home_service_manifest_no_credentials_endpoint)
    manifest.pop("service_endpoint", None)
    return manifest


@contextmanager
def _service_client_for(
    manifest: dict[str, Any], service_handler: _ServiceHttpHandler
) -> Iterator[TestClient]:
    """`_build_service_client` as a context manager — for one-off manifest
    variants that don't warrant a dedicated fixture."""
    yield from _build_service_client(manifest, service_handler)


@pytest.fixture
def timed_service_client(
    monkeypatch: pytest.MonkeyPatch,
    service_handler: _ServiceHttpHandler,
    home_service_manifest: dict[str, Any],
) -> Iterator[TestClient]:
    """`service_client` on a fake clock: the manifest lookup burns
    `_LOOKUP_SECONDS`, the probe `_PROBE_SECONDS`, nothing else moves time."""
    clock = _FakeClock()
    monkeypatch.setattr(web_server, "time", clock)
    service_handler.on_request = lambda: clock.advance(_PROBE_SECONDS)
    yield from _build_service_client(
        home_service_manifest,
        service_handler,
        on_manifest_read=lambda: clock.advance(_LOOKUP_SECONDS),
    )


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
    service_client_no_credentials_endpoint: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    """A manifest with a credentials_schema but no credentials_endpoint stores
    to keyring and reports pushed=False without attempting an HTTP push."""
    resp = service_client_no_credentials_endpoint.put(
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
    assert isinstance(data["latency_ms"], float)
    assert data["latency_ms"] >= 0.0
    assert data["latency_ms"] == round(data["latency_ms"], 1)


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
    assert isinstance(data["latency_ms"], float)
    assert data["latency_ms"] >= 0.0
    assert data["latency_ms"] == round(data["latency_ms"], 1)


def test_status_unknown_name_404(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations/nonexistent/status")
    assert resp.status_code == 404


def test_status_no_endpoint_has_null_latency(
    service_handler: _ServiceHttpHandler,
    home_service_manifest_without_any_endpoint: dict[str, Any],
) -> None:
    with _service_client_for(home_service_manifest_without_any_endpoint, service_handler) as client:
        resp = client.get("/api/integrations/home-service/status")
    data = resp.json()
    assert data["healthy"] is False
    assert data["detail"] == {"error": "no endpoint declared"}
    assert data["latency_ms"] is None


def test_status_times_the_probe_not_the_manifest_lookup(timed_service_client: TestClient) -> None:
    """latency_ms reports the probe alone, rounded to one decimal.

    The fake clock charges 500 s to the manifest lookup and 2.1239 ms to the
    probe, so starting the timer before the lookup, or rounding to anything but
    one decimal, cannot yield 2.1.
    """
    resp = timed_service_client.get("/api/integrations/home-service/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is True
    assert data["latency_ms"] == _EXPECTED_PROBE_MS


def test_status_unexpected_probe_error_is_unhealthy_not_500(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    """The narrow except tuple can't enumerate every failure — a closed
    app.state.http raises RuntimeError, which is neither HTTPError, InvalidURL
    nor ValueError. The operator gets an unhealthy row, not a 500."""
    service_handler.crash = RuntimeError("Cannot send a request, as the client has been closed.")
    resp = service_client.get("/api/integrations/home-service/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is False
    assert data["detail"]["error"].startswith("RuntimeError: ")
    assert isinstance(data["latency_ms"], float)


def test_status_times_the_adapter_probe_not_its_construction(
    monkeypatch: pytest.MonkeyPatch,
    service_handler: _ServiceHttpHandler,
    home_service_manifest: dict[str, Any],
) -> None:
    """Adapter path: `IntegrationRegistry.get` constructs lazily and reads the
    keyring, so a cold first call must not bill that to the probe."""
    clock = _FakeClock()
    monkeypatch.setattr(web_server, "time", clock)
    monkeypatch.setattr(_TimedAdapter, "clock", clock)
    with _service_client_for(home_service_manifest, service_handler) as client:
        IntegrationRegistry._registry["timed_adapter"] = _TimedAdapter
        IntegrationRegistry._instances.pop("timed_adapter", None)  # force a cold get()
        resp = client.get("/api/integrations/timed_adapter/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is True
    assert data["latency_ms"] == _EXPECTED_PROBE_MS


def test_status_adapter_construction_failure_has_null_latency(
    service_handler: _ServiceHttpHandler, home_service_manifest: dict[str, Any]
) -> None:
    """An adapter that can't even be built was never probed — no latency to report."""

    class _BrokenAdapter(_TimedAdapter):
        def __init__(self) -> None:
            raise RuntimeError("keyring locked")

    with _service_client_for(home_service_manifest, service_handler) as client:
        IntegrationRegistry._registry["timed_adapter"] = _BrokenAdapter
        IntegrationRegistry._instances.pop("timed_adapter", None)
        resp = client.get("/api/integrations/timed_adapter/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is False
    assert data["latency_ms"] is None


@pytest.mark.parametrize("endpoint", ["http://[::1", "http://\x00bad"])
def test_status_malformed_endpoint_is_unhealthy_not_500(
    endpoint: str,
    service_handler: _ServiceHttpHandler,
    home_service_manifest_without_any_endpoint: dict[str, Any],
) -> None:
    """A service can write any string into its manifest. An unclosed IPv6 host
    raises ValueError out of urljoin; a non-printable byte in the host raises
    httpx.InvalidURL, which is *not* an HTTPError. Neither may reach the client
    as a 500."""
    manifest = {**home_service_manifest_without_any_endpoint, "service_endpoint": endpoint}
    with _service_client_for(manifest, service_handler) as client:
        resp = client.get("/api/integrations/home-service/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is False
    assert "error" in data["detail"]
    assert isinstance(data["latency_ms"], float)


def test_status_non_string_endpoint_has_null_latency(
    service_handler: _ServiceHttpHandler,
    home_service_manifest_without_any_endpoint: dict[str, Any],
) -> None:
    """A non-string endpoint is "nothing to probe", not a urljoin crash."""
    manifest: dict[str, Any] = {
        **home_service_manifest_without_any_endpoint,
        "service_endpoint": 123,
    }
    with _service_client_for(manifest, service_handler) as client:
        resp = client.get("/api/integrations/home-service/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is False
    assert data["detail"] == {"error": "no endpoint declared"}
    assert data["latency_ms"] is None


def test_put_credentials_requires_session(anon_service_client: TestClient) -> None:
    """Credential writes are double-gated: trusted network AND passkey session."""
    resp = anon_service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://ha.local:8123", "token": "t"},
    )
    assert resp.status_code == 401


def test_delete_credentials_requires_session(anon_service_client: TestClient) -> None:
    resp = anon_service_client.delete("/api/integrations/home-service/credentials")
    assert resp.status_code == 401


def test_list_integrations_requires_session(anon_service_client: TestClient) -> None:
    """The listing leaks every credentials_schema and per-field configured map —
    session-gated, but NOT network-gated (the PWA reads it from the public host)."""
    resp = anon_service_client.get("/api/integrations")
    assert resp.status_code == 401


def test_integration_status_requires_session(anon_service_client: TestClient) -> None:
    """Status proxies the service's /health payload — session-gated."""
    resp = anon_service_client.get("/api/integrations/home-service/status")
    assert resp.status_code == 401
