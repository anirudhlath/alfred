# PWA Phase 0 — Backend Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the channels server safe to publish at `https://alfred.<domain>` behind nginx-proxy-manager + Cloudflare, so the PWA client can be built against a real, internet-facing API.

**Architecture:** Six independent hardening changes to the existing FastAPI channels process, no new service. uvicorn is told which proxy to believe for `X-Forwarded-*` (so the trusted-network gate sees the real client IP and cookies become `Secure`). The admin router drops its network gate (authenticated passkey session only, reads *and* controls), while every endpoint that can mint or widen credentials keeps the network gate **and** gains the session gate. Vite's hashed `/assets/*` become immutable and the entry point stays uncached. Both WebSockets answer `{"type":"ping"}` so Cloudflare's ~100 s idle timeout stops churning connections. Session TTL drops to 8 h.

**Tech Stack:** Python 3.13, FastAPI + Starlette, uvicorn 0.42 (`ProxyHeadersMiddleware`), redis.asyncio, pytest + pytest-asyncio (`asyncio_mode = auto`), loguru in `core/channels` and `core/identity`.

**Spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md` §3 (3.1, 3.2, 3.3, 3.4, 3.6) and §8 Phase 0. The follow-on plan `2026-09-04-pwa-phase0b-backend-additions.md` depends on this one merging first.

---

## Before you start

**Worktree.** The spec lives on branch `docs/pwa-client-design` (unmerged). Implementation gets its own branch off `master`:

```bash
cd ~/code/alfred-deploy/alfred
git fetch origin
git worktree add ~/code/.worktrees/alfred/pwa-phase0-security -b feat/pwa-phase0-security origin/master
cd ~/code/.worktrees/alfred/pwa-phase0-security
uv sync
```

Never commit from `~/code/alfred-deploy/alfred` itself — it is the live deploy checkout and a merge to `master` deploys automatically.

**The repo is public.** No real hostnames or IPs in code, tests, docs or commit messages. Use `alfred.<domain>`, `<host-lan-ip>`, RFC 5737 documentation addresses (`203.0.113.0/24`, `198.51.100.0/24`) and the Docker range `172.16.0.0/12` in examples.

**Running checks.** Always through `uv run`:

```bash
uv run pytest tests/core/channels tests/core/identity -q
uv run ruff check . && uv run ruff format --check .
uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
```

mypy is strict with the pydantic plugin; line length is 100. New tests go under `tests/` (not next to the module).

**Logging style.** `core/channels/*` and `core/identity/*` use **loguru** — brace placeholders (`logger.info("x={}", x)`), never `%s`.

**Commit style.** Conventional commits (`feat:`, `fix:`, `test:`, `docs:`). The `pr-title` CI check enforces the same format on the PR title.

**What the tests fake.** `tests/core/channels/conftest.py` provides `web_client`: `create_app()` with an `AsyncMock` Redis whose `hgetall` returns an authenticated session for cookie `alfred_auth=test-auth-session`. Starlette's `TestClient` reports the peer as the literal host string `"testclient"`, which `require_trusted_network` bypasses explicitly — so anything gated only by network passes in tests, and the way to exercise the gate is to feed it a different `request.client.host` (or rewrite it through uvicorn's `ProxyHeadersMiddleware`, see Task 1).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `core/channels/__main__.py` | Modify (line 38) | Pass `proxy_headers` / `forwarded_allow_ips` to uvicorn explicitly; log the trusted proxy list |
| `core/channels/admin_api.py` | Modify (lines 1–9, 41–46, 206–210) | Admin router gated by `require_authenticated` only |
| `core/channels/web_server.py` | Modify (lines 600–625, 789–812, 822–833) | Session gate on credential-equivalent endpoints; `/ws` ping; swap cache middleware |
| `core/channels/spa.py` | Modify (append) | `SpaCacheMiddleware` — immutable hashed assets, uncached entry point |
| `core/channels/telemetry_ws.py` | Modify (lines 106–124) | `/ws/telemetry` ping |
| `core/identity/auth_routes.py` | Modify (line 35) | Session TTL 8 h |
| `tests/core/channels/test_channels_main.py` | Create | uvicorn kwargs |
| `tests/core/channels/test_trusted_network.py` | Modify (append) | Proxy-header → gate chain |
| `tests/core/channels/test_admin_api.py` | Modify (lines 61–69) | Admin works from an untrusted network when authenticated |
| `tests/core/channels/test_device_registration.py` | Modify (fixtures + new tests) | Auth cookie; 401 without it |
| `tests/core/channels/test_service_integrations_api.py` | Modify (append) | 401 on credential writes without a session |
| `tests/core/channels/test_spa.py` | Modify (append) | Cache-Control per path class |
| `tests/core/channels/test_web_server.py` | Modify (append) | `/ws` ping is a no-op |
| `tests/core/channels/test_telemetry_ws.py` | Modify (append) | `/ws/telemetry` ping is a no-op |
| `tests/core/identity/test_auth_routes.py` | Modify (append) | TTL 8 h on session + cookie; `Secure` cookie behind `X-Forwarded-Proto` |
| `.env.example` | Modify (lines 114–118) | `FORWARDED_ALLOW_IPS`, strict-mode wording |
| `docs/deployment.md` | Modify (lines 109–120) | "Behind a reverse proxy" section; troubleshooting row |
| `docs/admin-api.md` | Modify (lines 23–43) | Auth model: session only |
| `docs/webauthn.md` | Modify (lines 14, 50, 55) | 8 h TTL; RP ID is the hostname; gate wording |
| `CLAUDE.md` | Modify (lines 78, 101, 282) | Project gotchas: gate layout + session TTL |
| `docs/backlog/low/web-asset-cache-headers.md` | Delete | Done by Task 5 |

---

### Task 1: Trust the reverse proxy's `X-Forwarded-*` headers

uvicorn 0.42 already runs `ProxyHeadersMiddleware` (`proxy_headers=True` is its default) and reads `FORWARDED_ALLOW_IPS` from the environment (default `127.0.0.1`). Behind NPM the connecting peer is the proxy's container/bridge address, so nothing is rewritten today: `request.client.host` is the proxy, every public request looks like it comes from the Docker network, and `secure=request.url.scheme == "https"` in `auth_routes.py` is always false. Make the wiring explicit in code and log it, so the operator can see what the process trusts.

**Files:**
- Modify: `core/channels/__main__.py:36-39`
- Create: `tests/core/channels/test_channels_main.py`
- Modify: `tests/core/channels/test_trusted_network.py` (append)

- [ ] **Step 1: Write the failing test for the uvicorn kwargs**

Create `tests/core/channels/test_channels_main.py`:

```python
"""Channels entrypoint — uvicorn is told which proxy to trust for X-Forwarded-*."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, "127.0.0.1"),  # uvicorn's own default: trust loopback only
        ("172.18.0.5", "172.18.0.5"),
        ("172.16.0.0/12,127.0.0.1", "172.16.0.0/12,127.0.0.1"),
    ],
)
def test_main_passes_forwarded_allow_ips(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: str
) -> None:
    import core.channels.__main__ as entry

    if env_value is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", env_value)
    monkeypatch.setenv("CHANNELS_PORT", "18081")

    with (
        patch.object(entry, "create_app", return_value=MagicMock()),
        patch.object(entry.uvicorn, "run") as run,
    ):
        entry.main()

    kwargs = run.call_args.kwargs
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == expected
    assert kwargs["port"] == 18081
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/core/channels/test_channels_main.py -v`
Expected: 3 × FAIL with `KeyError: 'proxy_headers'`

- [ ] **Step 3: Make the uvicorn call explicit**

In `core/channels/__main__.py` replace lines 35–39:

```python
    app = create_app(redis_url=config.redis_url)
    port = int(os.getenv("CHANNELS_PORT", "8081"))
    # Behind a reverse proxy the peer address is the proxy; uvicorn rewrites
    # request.client / scheme from X-Forwarded-For / -Proto, but only for peers in
    # FORWARDED_ALLOW_IPS (IPs or CIDRs). Everything downstream — the trusted-network
    # gate, Secure cookies, the 403 detail naming the client — depends on this.
    forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    logger.info("Trusting X-Forwarded-* headers from: {}", forwarded_allow_ips)
    for attempt in range(5):
        try:
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=port,
                log_level="info",
                proxy_headers=True,
                forwarded_allow_ips=forwarded_allow_ips,
            )
            break
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/core/channels/test_channels_main.py -v`
Expected: 3 × PASS

- [ ] **Step 5: Write the failing chain test — proxy header reaches the gate**

Append to `tests/core/channels/test_trusted_network.py`:

```python
def test_forwarded_for_from_trusted_proxy_reaches_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through uvicorn's ProxyHeadersMiddleware the gate sees the *forwarded* client,
    not the proxy — so a public caller behind NPM is rejected by IP, and the 403
    names that IP (the operator uses it to pick FORWARDED_ALLOW_IPS)."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS_STRICT", raising=False)

    app = FastAPI()

    @app.get("/gated", dependencies=[Depends(require_trusted_network)])
    async def gated() -> dict[str, bool]:
        return {"ok": True}

    # TestClient's peer is the literal "testclient"; trust it as the proxy.
    client = TestClient(ProxyHeadersMiddleware(app, trusted_hosts="testclient"))

    public = client.get("/gated", headers={"X-Forwarded-For": "203.0.113.9"})
    assert public.status_code == 403
    assert "203.0.113.9" in public.json()["detail"]

    lan = client.get("/gated", headers={"X-Forwarded-For": "192.168.1.20"})
    assert lan.status_code == 200


def test_forwarded_for_from_untrusted_peer_is_ignored() -> None:
    """A peer that is not in FORWARDED_ALLOW_IPS cannot spoof its way in or out."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app = FastAPI()

    @app.get("/gated", dependencies=[Depends(require_trusted_network)])
    async def gated() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(ProxyHeadersMiddleware(app, trusted_hosts="10.9.9.9"))
    # Header ignored → peer stays "testclient" → the test bypass applies → 200.
    resp = client.get("/gated", headers={"X-Forwarded-For": "203.0.113.9"})
    assert resp.status_code == 200
```

- [ ] **Step 6: Run them**

Run: `uv run pytest tests/core/channels/test_trusted_network.py -v`
Expected: all PASS (these document existing uvicorn behaviour; the gate code does not change). If `test_forwarded_for_from_trusted_proxy_reaches_gate` fails on the `192.168.1.20` case, check that `ALFRED_TRUSTED_NETWORKS_STRICT` is not set in your shell — the test deletes it, but the `monkeypatch` must come before the request.

- [ ] **Step 7: Lint, type-check, commit**

```bash
uv run ruff check core/channels/__main__.py tests/core/channels/test_channels_main.py tests/core/channels/test_trusted_network.py
uv run ruff format core/channels/__main__.py tests/core/channels/test_channels_main.py tests/core/channels/test_trusted_network.py
uv run mypy core/channels/__main__.py
git add core/channels/__main__.py tests/core/channels/test_channels_main.py tests/core/channels/test_trusted_network.py
git commit -m "feat(channels): trust X-Forwarded-* from FORWARDED_ALLOW_IPS explicitly"
```

---

### Task 2: Admin API — authenticated session only

Spec §3.2: admin reads and controls are gated by the passkey session alone. The network gate stays on credential-equivalent endpoints (Task 3). Today `create_admin_router(trusted_network_dep)` applies both.

**Files:**
- Modify: `core/channels/admin_api.py:1-9, 41-46, 206-210`
- Modify: `core/channels/web_server.py:822`
- Modify: `tests/core/channels/test_admin_api.py:61-69`

- [ ] **Step 1: Replace the failing-by-design test**

In `tests/core/channels/test_admin_api.py`, delete `test_admin_requires_trusted_network` (lines 61–69) and put this in its place:

```python
def test_admin_reads_and_controls_need_only_a_session() -> None:
    """Admin is gated by the passkey session alone — a public caller with a valid
    cookie gets reads AND controls. The network gate is reserved for endpoints that
    can mint or widen credentials (registration, credential writes, device tokens)."""
    r = _overview_redis()
    r.set = AsyncMock()
    client = make_admin_client(r)

    async def _untrusted(request: Request) -> None:
        raise HTTPException(status_code=403, detail="untrusted")

    # Even if the process-wide network gate rejected everyone, admin must not care.
    client.app.dependency_overrides[require_trusted_network] = _untrusted  # type: ignore[attr-defined]
    try:
        assert client.get("/api/admin/overview").status_code == 200
        assert client.post("/api/admin/dnd", json={"active": True}).status_code == 200
    finally:
        client.app.dependency_overrides.pop(require_trusted_network, None)  # type: ignore[attr-defined]
```

Add `Request` to the fastapi import at the top of the file:

```python
from fastapi import HTTPException, Request
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/core/channels/test_admin_api.py::test_admin_reads_and_controls_need_only_a_session -v`
Expected: FAIL — `assert 403 == 200`

- [ ] **Step 3: Drop the network dependency from the router**

In `core/channels/admin_api.py`:

Replace the module docstring (lines 1–9):

```python
"""Admin API — read-only observability + curated controls for the web app.

Every endpoint requires an authenticated passkey session (``require_authenticated``,
HTTP 401 otherwise) and nothing else: admin reads and controls are usable from the
public hostname once signed in. The trusted-network gate is reserved for endpoints
that can mint or widen credentials — passkey registration, credential writes, device
tokens, voice enrolment — which live in ``web_server.py`` / ``auth_routes.py``.

Reads are defensive: missing keys/streams/files yield empty results, never 500s.
Controls map to operations the system already performs — direct Redis writes
for shared state, ACTIONS_STREAM publishes for process-owned behavior.
"""
```

Remove the now-unused `Callable` import: change lines 41–46 from

```python
if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from shared.types import AioRedis
```

to

```python
if TYPE_CHECKING:
    import httpx

    from shared.types import AioRedis
```

Replace the router factory signature (lines 206–210):

```python
def create_admin_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/admin",
        dependencies=[Depends(require_authenticated)],
    )
```

- [ ] **Step 4: Update the call site**

In `core/channels/web_server.py` line 822:

```python
    app.include_router(create_admin_router())
```

- [ ] **Step 5: Run the admin tests**

Run: `uv run pytest tests/core/channels/test_admin_api.py -v`
Expected: all PASS (including `test_admin_requires_auth_cookie`, which still expects 401).

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check core/channels/admin_api.py core/channels/web_server.py tests/core/channels/test_admin_api.py
uv run ruff format core/channels/admin_api.py core/channels/web_server.py tests/core/channels/test_admin_api.py
uv run mypy core/channels/
git add core/channels/admin_api.py core/channels/web_server.py tests/core/channels/test_admin_api.py
git commit -m "feat(admin): gate the admin API by passkey session only"
```

---

### Task 3: Credential-equivalent endpoints require session **and** trusted network

`PUT/DELETE /api/integrations/{name}/credentials` and `POST/DELETE /api/devices/register` are network-gated only. On the public host that means "anyone on the LAN" — add the session gate. (`POST /api/voice/enroll` already has both; passkey registration is handled in the 0b plan's pairing-code task and stays network-only here.)

**Files:**
- Modify: `core/channels/web_server.py:600-603, 620-623, 789-792, 809-812`
- Modify: `tests/core/channels/test_device_registration.py`
- Modify: `tests/core/channels/test_service_integrations_api.py` (append)

- [ ] **Step 1: Give the device-registration tests a session, and add the 401 cases**

Replace the top of `tests/core/channels/test_device_registration.py` (lines 1–31) with:

```python
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from shared.streams import AUTH_SESSION_PREFIX

_SESSION = "device-test-session"


@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock.hset = AsyncMock()
    mock.hdel = AsyncMock()
    mock.close = AsyncMock()
    mock.xread = AsyncMock(return_value=[])

    async def _fake_hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_SESSION}":
            return {b"authenticated": b"1"}
        return {}

    mock.hgetall = AsyncMock(side_effect=_fake_hgetall)
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
    """Signed-in client. Device tokens are credential-equivalent: the endpoint needs
    a passkey session *and* a trusted network (TestClient's peer is trusted)."""
    c = TestClient(app)
    c.cookies.set("alfred_auth", _SESSION)
    return c


@pytest.fixture
def anon_client(app):
    return TestClient(app)


def test_register_device_requires_session(anon_client, mock_redis) -> None:
    resp = anon_client.post(
        "/api/devices/register",
        json={
            "device_token": "aabbccdd11223344aabbccdd11223344",
            "platform": "ios",
            "identity": "sir",
        },
    )
    assert resp.status_code == 401
    mock_redis.hset.assert_not_called()


def test_unregister_device_requires_session(anon_client, mock_redis) -> None:
    resp = anon_client.request(
        "DELETE",
        "/api/devices/register",
        json={"device_token": "aabbccdd11223344aabbccdd11223344"},
    )
    assert resp.status_code == 401
    mock_redis.hdel.assert_not_called()
```

Leave the existing `test_register_device_stores_token` … `test_register_device_invalid_platform_rejected` tests untouched below; they now run with the cookie.

- [ ] **Step 2: Add the 401 cases for credential writes**

Append to `tests/core/channels/test_service_integrations_api.py`:

```python
def test_put_credentials_requires_session(service_client: TestClient) -> None:
    """Credential writes are double-gated: trusted network AND passkey session."""
    service_client.cookies.clear()
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://ha.local:8123", "token": "t"},
    )
    assert resp.status_code == 401


def test_delete_credentials_requires_session(service_client: TestClient) -> None:
    service_client.cookies.clear()
    resp = service_client.delete("/api/integrations/home-service/credentials")
    assert resp.status_code == 401
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/core/channels/test_device_registration.py tests/core/channels/test_service_integrations_api.py -v -k "requires_session"`
Expected: 4 × FAIL — `assert 200 == 401` (or 404/502 for the credentials cases; anything but 401)

- [ ] **Step 4: Add the session dependency to the four endpoints**

In `core/channels/web_server.py` change each of these four decorators. Lines 600–603:

```python
    @app.put(
        "/api/integrations/{name}/credentials",
        dependencies=[Depends(require_trusted_network), Depends(require_authenticated)],
    )
```

Lines 620–623:

```python
    @app.delete(
        "/api/integrations/{name}/credentials",
        dependencies=[Depends(require_trusted_network), Depends(require_authenticated)],
    )
```

Lines 789–792:

```python
    @app.post(
        "/api/devices/register",
        dependencies=[Depends(require_trusted_network), Depends(require_authenticated)],
    )
```

Lines 809–812:

```python
    @app.delete(
        "/api/devices/register",
        dependencies=[Depends(require_trusted_network), Depends(require_authenticated)],
    )
```

`require_authenticated` is already imported at line 29.

- [ ] **Step 5: Run the affected suites**

Run: `uv run pytest tests/core/channels/test_device_registration.py tests/core/channels/test_service_integrations_api.py tests/core/channels/test_settings_api.py tests/core/channels/test_service_credentials.py -v`
Expected: all PASS

- [ ] **Step 6: Lint, commit**

```bash
uv run ruff check core/channels/web_server.py tests/core/channels/test_device_registration.py tests/core/channels/test_service_integrations_api.py
uv run ruff format core/channels/web_server.py tests/core/channels/test_device_registration.py tests/core/channels/test_service_integrations_api.py
git add core/channels/web_server.py tests/core/channels/test_device_registration.py tests/core/channels/test_service_integrations_api.py
git commit -m "feat(channels): require a passkey session on credential and device-token writes"
```

---

### Task 4: Session TTL 24 h → 8 h, and `Secure` cookies behind the proxy

**Files:**
- Modify: `core/identity/auth_routes.py:35`
- Modify: `tests/core/identity/test_auth_routes.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/identity/test_auth_routes.py`:

```python
class TestSessionLifetime:
    """Sessions last 8 hours (spec §3.2) and the cookie is Secure whenever the
    request arrived over HTTPS — including via a trusted reverse proxy."""

    def _complete_login(
        self, store: CredentialStore, redis_mock: AsyncMock, *, wrap: object = None
    ) -> tuple[TestClient, object]:
        from webauthn.helpers import bytes_to_base64url

        stored_b64 = bytes_to_base64url(b"\x01\x02\x03\x04")
        redis_mock.get = AsyncMock(return_value=stored_b64.encode())

        app = FastAPI()
        app.include_router(create_auth_router(store=store, redis=redis_mock))
        client = TestClient(wrap(app) if callable(wrap) else app)
        return client, app

    @pytest.mark.asyncio
    async def test_session_and_cookie_last_eight_hours(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        await store.save_credential(
            credential_id="AQID",
            public_key=b"\x03",
            sign_count=0,
            device_name="Phone",
            transports=["internal"],
        )
        client, _ = self._complete_login(store, redis_mock)

        verification = MagicMock()
        verification.new_sign_count = 1
        with patch(
            "core.identity.auth_routes.verify_authentication_response",
            return_value=verification,
        ):
            resp = client.post(
                "/api/auth/login/complete",
                json={
                    "_challenge_id": "c1",
                    "id": "AQID",
                    "rawId": "AQID",
                    "type": "public-key",
                    "response": {
                        "clientDataJSON": "e30",
                        "authenticatorData": "e30",
                        "signature": "e30",
                    },
                },
            )

        assert resp.status_code == 200
        _key, ttl = redis_mock.expire.call_args[0]
        assert ttl == 8 * 3600
        cookie = resp.headers["set-cookie"]
        assert "Max-Age=28800" in cookie
        assert "Secure" not in cookie  # plain http in this test

    @pytest.mark.asyncio
    async def test_cookie_is_secure_behind_https_proxy(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        await store.save_credential(
            credential_id="AQID",
            public_key=b"\x03",
            sign_count=0,
            device_name="Phone",
            transports=["internal"],
        )
        client, _ = self._complete_login(
            store,
            redis_mock,
            wrap=lambda app: ProxyHeadersMiddleware(app, trusted_hosts="testclient"),
        )

        verification = MagicMock()
        verification.new_sign_count = 1
        with patch(
            "core.identity.auth_routes.verify_authentication_response",
            return_value=verification,
        ):
            resp = client.post(
                "/api/auth/login/complete",
                headers={"X-Forwarded-Proto": "https"},
                json={
                    "_challenge_id": "c1",
                    "id": "AQID",
                    "rawId": "AQID",
                    "type": "public-key",
                    "response": {
                        "clientDataJSON": "e30",
                        "authenticatorData": "e30",
                        "signature": "e30",
                    },
                },
            )

        assert resp.status_code == 200
        assert "Secure" in resp.headers["set-cookie"]
```

- [ ] **Step 2: Run them to verify the TTL test fails**

Run: `uv run pytest tests/core/identity/test_auth_routes.py::TestSessionLifetime -v`
Expected: `test_session_and_cookie_last_eight_hours` FAIL with `assert 86400 == 28800`; `test_cookie_is_secure_behind_https_proxy` PASS (documents the Task 1 dependency — the cookie code already keys off `request.url.scheme`).

- [ ] **Step 3: Change the TTL**

In `core/identity/auth_routes.py` line 35:

```python
_AUTH_SESSION_TTL = 8 * 3600  # 8 hours — a phone re-auths with Face ID, cheap to renew
```

- [ ] **Step 4: Run the identity suite**

Run: `uv run pytest tests/core/identity -v`
Expected: all PASS

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff check core/identity/auth_routes.py tests/core/identity/test_auth_routes.py
uv run ruff format core/identity/auth_routes.py tests/core/identity/test_auth_routes.py
git add core/identity/auth_routes.py tests/core/identity/test_auth_routes.py
git commit -m "feat(auth): shorten passkey sessions to 8 hours"
```

---

### Task 5: Cache headers — immutable `/assets/*`, uncached entry point

Vite hashes every file under `/assets/`; `index.html` (and every SPA-fallback route that serves it) must never be cached or a deploy strands users on old bundles. The inner `NoCacheStaticMiddleware` in `create_app` stamps `no-cache` on `.css/.js/.html` — which defeats the hashing and misses fallback routes like `/activity`. Replace it with a module-level middleware in `spa.py` that can be tested directly.

**Files:**
- Modify: `core/channels/spa.py` (append)
- Modify: `core/channels/web_server.py:21, 825-832`
- Modify: `tests/core/channels/test_spa.py` (append)
- Delete: `docs/backlog/low/web-asset-cache-headers.md`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/channels/test_spa.py`:

```python
_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_STORE = "no-cache, no-store, must-revalidate"


def _cached_app(tmp_path: Path) -> TestClient:
    from core.channels.spa import SpaCacheMiddleware

    app = FastAPI()

    @app.get("/api/thing")
    async def thing() -> dict[str, bool]:
        return {"ok": True}

    mount_spa(app, _dist(tmp_path))
    app.add_middleware(SpaCacheMiddleware)
    return TestClient(app)


def test_hashed_assets_are_immutable(tmp_path: Path) -> None:
    client = _cached_app(tmp_path)
    assert client.get("/assets/app.js").headers["cache-control"] == _IMMUTABLE


def test_entry_point_is_never_cached(tmp_path: Path) -> None:
    client = _cached_app(tmp_path)
    for path in ("/", "/index.html", "/activity"):
        assert client.get(path).headers["cache-control"] == _NO_STORE, path


def test_api_responses_are_left_alone(tmp_path: Path) -> None:
    client = _cached_app(tmp_path)
    assert "cache-control" not in client.get("/api/thing").headers
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/channels/test_spa.py -v`
Expected: 3 × FAIL with `ImportError: cannot import name 'SpaCacheMiddleware'`

- [ ] **Step 3: Implement the middleware**

Replace the import block at the top of `core/channels/spa.py` (lines 1–16) with:

```python
"""SPA serving — static assets + index.html fallback for client-side routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response

_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_STORE = "no-cache, no-store, must-revalidate"
```

Then append to the end of the file:

```python


class SpaCacheMiddleware(BaseHTTPMiddleware):
    """Cache policy for the SPA. Vite content-hashes everything under ``/assets/``,
    so those responses are immutable for a year; every other non-API path
    (``/``, ``/index.html``, SPA-fallback routes, the service worker, the manifest)
    is the un-hashed entry surface and must be revalidated on every load so a deploy
    takes effect immediately. ``/api/*`` is left untouched.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response: Response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = _IMMUTABLE
        elif not path.startswith("/api/"):
            response.headers["Cache-Control"] = _NO_STORE
        return response
```

- [ ] **Step 4: Swap it into `create_app`**

In `core/channels/web_server.py` replace lines 825–832:

```python
    class NoCacheStaticMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
            response: Response = await call_next(request)
            if request.url.path.endswith((".css", ".js", ".html")):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

    app.add_middleware(NoCacheStaticMiddleware)
```

with

```python
    app.add_middleware(SpaCacheMiddleware)
```

Add the import next to the other `core.channels` imports (after line 34):

```python
from core.channels.spa import SpaCacheMiddleware
```

Then remove `BaseHTTPMiddleware, RequestResponseEndpoint` from line 21 and `Response` from the starlette responses import **only if** nothing else in `web_server.py` uses them — run `uv run ruff check core/channels/web_server.py`; it reports each unused import (F401) and you delete exactly those.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/core/channels/test_spa.py tests/core/channels/test_web_server.py -v`
Expected: all PASS. `test_auth_status_not_shadowed_by_spa_catch_all` still passes — it only checks status and body.

- [ ] **Step 6: Retire the backlog note, lint, commit**

```bash
git rm docs/backlog/low/web-asset-cache-headers.md
uv run ruff check core/channels/spa.py core/channels/web_server.py tests/core/channels/test_spa.py
uv run ruff format core/channels/spa.py core/channels/web_server.py tests/core/channels/test_spa.py
uv run mypy core/channels/
git add core/channels/spa.py core/channels/web_server.py tests/core/channels/test_spa.py
git commit -m "feat(web): immutable cache for hashed assets, no-store for the SPA entry point"
```

---

### Task 6: `{"type":"ping"}` is a no-op on both WebSockets

Cloudflare closes idle proxied WebSockets after ~100 s. The client will send `{"type":"ping"}` every 30 s. Today `/ws` answers it with an error frame and — worse — it counts as the "first message", locking the session before the client's real first message can restore a previous `session_id`. `/ws/telemetry` answers it with a spurious `subscribed` ack.

**Files:**
- Modify: `core/channels/web_server.py:440-445`
- Modify: `core/channels/telemetry_ws.py:106-113`
- Modify: `tests/core/channels/test_web_server.py` (append)
- Modify: `tests/core/channels/test_telemetry_ws.py` (append)

- [ ] **Step 1: Write the failing `/ws` test**

Append to `tests/core/channels/test_web_server.py`:

```python
def test_ws_ping_is_a_no_op_and_does_not_lock_the_session(web_client: TestClient) -> None:
    """Keepalive pings get a pong and are otherwise invisible: the client's first
    *real* message can still restore a previous session_id after any number of pings."""
    from bus.schemas.events import AlfredResponse

    alfred_resp = AlfredResponse(
        source="conscious",
        channel="web_pwa",
        session_id="ignored-by-handler",
        text="Certainly, sir.",
    )
    with (
        patch("core.channels.web_server.publish_and_wait", new=AsyncMock(return_value=alfred_resp)),
        web_client.websocket_connect("/ws") as ws,
    ):
        assert ws.receive_json()["type"] == "session"

        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

        ws.send_json({"type": "text", "content": "hello", "session_id": "restored-session"})
        response = ws.receive_json()

    assert response["type"] == "response"
    assert response["session_id"] == "restored-session"
```

Check the top of the file already imports `AsyncMock` and `patch` from `unittest.mock` (it does — the existing `/ws` test uses both) and `AlfredResponse` — if `AlfredResponse` is imported at module level, drop the local import.

- [ ] **Step 2: Write the failing `/ws/telemetry` test**

Append to `tests/core/channels/test_telemetry_ws.py`:

```python
def test_telemetry_ws_ping_gets_pong_not_subscribed_ack() -> None:
    mock_redis = AsyncMock()
    mock_redis.xread = AsyncMock(side_effect=lambda *a, **k: asyncio.Event().wait())
    client = _make_client(mock_redis)
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
        # Subscriptions still work afterwards and the ack lists only real streams.
        ws.send_text(json.dumps({"type": "subscribe", "streams": ["events"]}))
        assert ws.receive_json() == {"type": "subscribed", "streams": ["events"]}
```

- [ ] **Step 3: Run both to verify they fail**

Run: `uv run pytest tests/core/channels/test_web_server.py::test_ws_ping_is_a_no_op_and_does_not_lock_the_session tests/core/channels/test_telemetry_ws.py::test_telemetry_ws_ping_gets_pong_not_subscribed_ack -v`
Expected: `/ws` test FAIL — `assert {"type": "error", "text": "Unsupported content type: ping", ...} == {"type": "pong"}`; telemetry test FAIL — `{"type": "subscribed", "streams": []} != {"type": "pong"}`

- [ ] **Step 4: Handle ping on `/ws` before the session lock**

In `core/channels/web_server.py`, replace lines 440–445:

```python
                data = await websocket.receive_json()

                # Allow client to restore a previous session on its first message only
                if not session_locked and (client_sid := data.get("session_id")):
                    session_id = client_sid
                session_locked = True
```

with

```python
                data = await websocket.receive_json()

                # Keepalive (Cloudflare drops proxied sockets idle ~100s). Answered
                # before the session-restore block so pings never count as the
                # client's first message.
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                # Allow client to restore a previous session on its first message only
                if not session_locked and (client_sid := data.get("session_id")):
                    session_id = client_sid
                session_locked = True
```

- [ ] **Step 5: Handle ping on `/ws/telemetry` before the subscribe ack**

In `core/channels/telemetry_ws.py`, replace lines 106–113:

```python
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "invalid JSON"})
                    continue
                names = [n for n in msg.get("streams", []) if n in STREAM_CATALOG]
```

with

```python
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "invalid JSON"})
                    continue
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                names = [n for n in msg.get("streams", []) if n in STREAM_CATALOG]
```

- [ ] **Step 6: Run the WebSocket suites**

Run: `uv run pytest tests/core/channels/test_web_server.py tests/core/channels/test_telemetry_ws.py tests/core/channels/test_web_websockets.py -v`
Expected: all PASS

- [ ] **Step 7: Lint, commit**

```bash
uv run ruff check core/channels/web_server.py core/channels/telemetry_ws.py tests/core/channels/test_web_server.py tests/core/channels/test_telemetry_ws.py
uv run ruff format core/channels/web_server.py core/channels/telemetry_ws.py tests/core/channels/test_web_server.py tests/core/channels/test_telemetry_ws.py
git add core/channels/web_server.py core/channels/telemetry_ws.py tests/core/channels/test_web_server.py tests/core/channels/test_telemetry_ws.py
git commit -m "feat(ws): answer {type: ping} with pong on /ws and /ws/telemetry"
```

---

### Task 7: Documentation and `.env.example`

Every doc that describes the old gate layout must change in the same PR, or the next operator reads the wrong rule.

**Files:**
- Modify: `.env.example:114-118`
- Modify: `docs/deployment.md:109-120` and the troubleshooting table
- Modify: `docs/admin-api.md:23-43`
- Modify: `docs/webauthn.md:14, 50, 53-58`
- Modify: `CLAUDE.md:78, 101, 282`

- [ ] **Step 1: `.env.example`**

Replace lines 114–118:

```
# WebAuthn/admin trust. Loopback + private LAN (RFC1918) + Tailscale are trusted by
# default; add extra CIDRs here (comma-separated). Set ALFRED_TRUSTED_NETWORKS_STRICT=1
# to trust ONLY loopback + Tailscale + the CIDRs you list here.
ALFRED_TRUSTED_NETWORKS=
ALFRED_TRUSTED_NETWORKS_STRICT=
```

with

```
# Trusted networks gate the endpoints that can mint or widen credentials: passkey
# registration, credential writes, device tokens, voice enrolment. (Admin reads and
# controls need only a signed-in passkey session.) Loopback + private LAN (RFC1918) +
# Tailscale are trusted by default; add extra CIDRs here (comma-separated).
# Set ALFRED_TRUSTED_NETWORKS_STRICT=1 to trust ONLY loopback + Tailscale + the CIDRs
# you list — REQUIRED when Alfred is reachable from the internet, or the whole
# RFC1918 space (every LAN a guest could be on) counts as trusted.
ALFRED_TRUSTED_NETWORKS=
ALFRED_TRUSTED_NETWORKS_STRICT=
# Behind a reverse proxy (nginx-proxy-manager, Caddy, …): the peer address the
# process sees is the proxy, not the browser. List the proxy's IP or CIDR here and
# uvicorn rewrites the client address and scheme from X-Forwarded-For / -Proto — the
# trusted-network gate, Secure cookies and the 403 detail all depend on it. Default
# trusts loopback only. Not sure of the proxy's address? Make one request through it
# to a gated endpoint: the 403 detail names the peer the process saw.
FORWARDED_ALLOW_IPS=
```

- [ ] **Step 2: `docs/deployment.md`**

Replace the "Access from your LAN" section (lines 109–120) with:

```markdown
## Access from your LAN

Endpoints that can mint or widen credentials — WebAuthn passkey **registration**,
credential writes, push device tokens, voice enrolment — are gated to trusted networks.
Everything else, including the admin API's reads and controls, needs only a signed-in
passkey session. By default Alfred trusts **loopback, private LAN (RFC1918), and
Tailscale** — so localhost, the Docker bridge, and your own home network all work with no
configuration. To reach it from another device, browse to the host's LAN IP on port 8081
and register a passkey.

- Add extra ranges with `ALFRED_TRUSTED_NETWORKS=10.1.2.0/24,…` (comma-separated).
- Lock it down with `ALFRED_TRUSTED_NETWORKS_STRICT=1` to trust **only** loopback,
  Tailscale, and the CIDRs you list explicitly (passkeys remain the primary auth either way).

A rejected request returns a 403 that names the offending IP and how to allow it.

## Behind a reverse proxy

When a proxy (nginx-proxy-manager, Caddy, Cloudflare in front of either) terminates TLS,
the process sees the proxy as the peer. Set `FORWARDED_ALLOW_IPS` to the proxy's address
or CIDR so uvicorn rewrites the client IP and scheme from `X-Forwarded-For` /
`X-Forwarded-Proto`; the trusted-network gate, `Secure` cookies and the 403 detail all
key off that. Then:

- **Set `ALFRED_TRUSTED_NETWORKS_STRICT=1` and list your LAN CIDR** in
  `ALFRED_TRUSTED_NETWORKS`. Public exposure with the permissive default would trust every
  RFC1918 range — i.e. any network a caller happens to be on.
- The proxy must send a **single** `X-Forwarded-For` holding the real client (behind
  Cloudflare, copy it from `CF-Connecting-IP`), plus `X-Forwarded-Proto: https`.
- **The hostname is the WebAuthn RP ID.** Passkeys are bound to it; renaming the host
  orphans every registered passkey. Pick it once.
- Proxied WebSockets are closed after ~100 s idle by Cloudflare; clients send
  `{"type":"ping"}` (answered with `{"type":"pong"}`) on `/ws` and `/ws/telemetry` to
  keep them open.
```

Add a row to the troubleshooting table:

```markdown
| 403 "not on a trusted network" naming the proxy's or Docker's address, from a device that *is* on the LAN | `FORWARDED_ALLOW_IPS` does not include the proxy — the gate is judging the proxy's address. Put the address the 403 names into `FORWARDED_ALLOW_IPS` and restart. |
```

- [ ] **Step 3: `docs/admin-api.md`**

Replace the "Auth Model" section (lines 23–43, up to but not including "### Telemetry WebSocket Auth") with:

```markdown
## Auth Model

Every admin endpoint — reads and controls alike — enforces exactly one FastAPI dependency:

| Dependency | Gate | Error |
|---|---|---|
| `require_authenticated` | `AuthCookieMiddleware` must have marked `request.state.authenticated = True` via a valid `alfred_auth` session cookie | HTTP 401 |

There is deliberately **no** trusted-network gate here: the admin API is usable from the
public hostname once signed in with a passkey. The network gate (`require_trusted_network`,
HTTP 403) is reserved for endpoints that can mint or widen credentials — passkey
registration, `PUT/DELETE /api/integrations/{name}/credentials`, `POST/DELETE
/api/devices/register`, `POST /api/voice/enroll` — which carry **both** dependencies.

The dependency is applied at router creation time:

```python
router = APIRouter(
    prefix="/api/admin",
    dependencies=[Depends(require_authenticated)],
)
```
```

- [ ] **Step 4: `docs/webauthn.md`**

Line 14 — change the sequence-diagram note:

```
    Note over B,DB: Registration (first visit; trusted network, or a pairing code from a signed-in device)
```

Line 50:

```
- **Auth Sessions:** Redis at `alfred:auth:{session_id}` -- 8h TTL
```

Replace the "Security Properties" list (lines 55–58) with:

```markdown
- Registration requires a trusted network (`require_trusted_network`); admin reads and
  controls need only the session
- **The RP ID is the request's `Host`.** Passkeys are bound to the hostname Alfred is
  served from — behind a public proxy that is the public hostname, which must therefore
  never change once passkeys exist
- Cookies: HttpOnly, SameSite=Strict, Secure (HTTPS — including behind a trusted proxy
  that sends `X-Forwarded-Proto`, see `FORWARDED_ALLOW_IPS`)
- Challenges: one-time use, 5min expiry
- Sign count verification on each login (clone detection)
```

- [ ] **Step 5: `CLAUDE.md`**

Three lines in the project `CLAUDE.md` state the old rules. Replace each line exactly.

Line 78, old:

```
- `core/channels/admin_api.py` — `create_admin_router()` (`/api/admin/*` — 11 reads + 6 controls, cookie + trusted-network gated)
```

new:

```
- `core/channels/admin_api.py` — `create_admin_router()` (`/api/admin/*` — 11 reads + 6 controls, session-cookie gated; the trusted-network gate is NOT applied here, see docs/admin-api.md "Auth Model")
```

Line 101, old:

```
- Auth sessions: Redis at `alfred:auth:{session_id}` — 24hr TTL, HttpOnly cookie `alfred_auth`
```

new:

```
- Auth sessions: Redis at `alfred:auth:{session_id}` — 8h TTL, HttpOnly cookie `alfred_auth` (Secure when the request arrived over HTTPS, including via a trusted proxy)
```

Line 282, old:

```
- `core/channels/admin_api.py` + `telemetry_ws.py` are gated by BOTH `require_trusted_network` AND `require_authenticated` (session cookie) — `/ws/telemetry` authenticates via the shared `authenticate_ws_cookie()` helper.
```

new:

```
- `core/channels/admin_api.py` is gated by `require_authenticated` (session cookie) only; the credential-equivalent writes (`/api/integrations/{name}/credentials` PUT/DELETE, `/api/devices` POST/DELETE) and WebAuthn registration keep BOTH gates. `/ws/telemetry` authenticates via the shared `authenticate_ws_cookie()` helper. `X-Forwarded-*` is honoured only from `FORWARDED_ALLOW_IPS` (uvicorn `proxy_headers`).
```

Check nothing else in `CLAUDE.md` describes the old layout:

```bash
grep -n "24hr\|gated by BOTH\|trusted-network gated" CLAUDE.md
```

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add .env.example docs/deployment.md docs/admin-api.md docs/webauthn.md CLAUDE.md
git commit -m "docs: describe the proxy-aware gate layout and 8h sessions"
```

---

### Task 8: Full verification

- [ ] **Step 1: Whole suite + static checks**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
```

Expected: all green. If `uv run pytest -q` reports failures outside `tests/core/channels` or `tests/core/identity`, they are pre-existing — confirm by running the same test on `origin/master` in the main checkout before touching it.

- [ ] **Step 2: Grep for stale references**

```bash
grep -rn "create_admin_router(require_trusted_network\|NoCacheStaticMiddleware\|24hr TTL\|24 hours" core/ docs/ tests/ web/ --include='*.py' --include='*.md' --include='*.ts' --include='*.tsx'
```

Expected: no output. (If `web/` — the old SPA — hard-codes any of these, leave it; it is replaced wholesale in Phase 1.)

- [ ] **Step 3: Manual smoke on the LAN (before any exposure)**

From the worktree, with Redis reachable:

```bash
uv run python -m core.channels
```

then from another shell:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8081/api/admin/overview      # 401 — no cookie
curl -s -D - -o /dev/null http://localhost:8081/ | grep -i cache-control                # no-cache, no-store, must-revalidate
curl -s -H 'X-Forwarded-For: 203.0.113.9' http://localhost:8081/api/devices/register -X POST -d '{}' -H 'content-type: application/json'
```

The last one returns 401 (session gate runs first, and the loopback peer is trusted by default so the header is honoured) — set a session cookie and it becomes 403 naming `203.0.113.9`. Stop the server.

**Operator steps after merge** (not part of this PR; recorded in the operator runbook outside git): `.env` gets `ALFRED_TRUSTED_NETWORKS_STRICT=1`, the LAN CIDR, and `FORWARDED_ALLOW_IPS` = the address the 403 detail names when the proxy makes a request; then the NPM proxy host, Cloudflare SSL mode, DNS, and the first passkey registration from the LAN — in that order.
