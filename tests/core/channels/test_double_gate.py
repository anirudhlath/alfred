"""The credential-equivalent routes are gated TWICE: trusted network AND passkey session.

The rest of the suite reaches these routes over TestClient's default peer, the literal
``"testclient"``, which ``require_trusted_network`` bypasses — so the network half of the
gate is never actually exercised there. These tests give TestClient a real, untrusted peer
address so both halves run, and pin the *order* the two gates run in.

Device registration is the exception: it takes a session from anywhere (spec §6.2, the
owner's decision of 2026-10-29), so a phone off the LAN can register for push.
"""

from __future__ import annotations

from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient

from core.channels.web_server import create_app
from shared.streams import AUTH_SESSION_PREFIX
from tests.core.channels.conftest import _TEST_SESSION_ID

_UNTRUSTED = ("203.0.113.9", 12345)  # TEST-NET-3 — never RFC1918, CGNAT or loopback

# (method, path, json body) for every route carrying _CREDENTIAL_GATES.
DOUBLE_GATED: list[tuple[str, str, dict[str, str] | None]] = [
    ("PUT", "/api/integrations/home-service/credentials", {"url": "http://x", "token": "t"}),
    ("DELETE", "/api/integrations/home-service/credentials", None),
    ("POST", "/api/voice/enroll", {"identity": "sir", "samples": []}),
]

_DEVICE_TOKEN = "aabbccdd11223344aabbccdd11223344"
# (method, json body) for the two device routes, which take a session alone.
DEVICE_ROUTES: list[tuple[str, dict[str, str]]] = [
    ("POST", {"device_token": _DEVICE_TOKEN, "platform": "ios", "identity": "sir"}),
    ("DELETE", {"device_token": _DEVICE_TOKEN}),
]
_CREDENTIALS = "/api/integrations/home-service/credentials"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real app, with an in-memory Redis that holds exactly one session.

    In memory rather than a mock: a device registration writes through a transaction.
    """
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS_STRICT", raising=False)

    server = fakeredis.FakeServer()
    fakeredis.FakeRedis(server=server).hset(
        f"{AUTH_SESSION_PREFIX}{_TEST_SESSION_ID}",
        mapping={"authenticated": "1", "credential_id": "test-cred"},
    )
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = fakeredis.FakeAsyncRedis(server=server)
    return app


def _client(app: Any, *, signed_in: bool, peer: tuple[str, int] = _UNTRUSTED) -> TestClient:
    client = TestClient(app, client=peer)
    if signed_in:
        client.cookies.set("alfred_auth", _TEST_SESSION_ID)
    return client


@pytest.mark.parametrize(("method", "path", "body"), DOUBLE_GATED)
def test_untrusted_peer_rejected_even_with_a_session(
    app: Any, method: str, path: str, body: dict[str, str] | None
) -> None:
    """A valid session is not enough: credential-equivalent writes also need the LAN."""
    resp = _client(app, signed_in=True).request(method, path, json=body)
    assert resp.status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), DOUBLE_GATED)
def test_trusted_peer_without_a_session_rejected(
    app: Any, method: str, path: str, body: dict[str, str] | None
) -> None:
    """...and the LAN is not enough either — "on the network" is not an identity."""
    resp = _client(app, signed_in=False, peer=("192.168.1.20", 12345)).request(
        method, path, json=body
    )
    assert resp.status_code == 401


def test_network_gate_runs_before_the_session_gate(app: Any) -> None:
    """Untrusted peer + NO cookie → 403, not 401.

    The anonymous cell is the only witness to the order. With a valid cookie the
    session gate passes either way, so a 403 there is produced by both orderings and
    proves nothing; it is asserted below only as the companion case. Signed out, the
    two orderings genuinely disagree — network-first refuses on the network (403),
    session-first refuses on the session (401) — and 401 is exactly the answer that
    would tell an anonymous internet caller their cookie is the thing that is missing.
    """
    assert _client(app, signed_in=False).delete(_CREDENTIALS).status_code == 403
    assert _client(app, signed_in=True).delete(_CREDENTIALS).status_code == 403


def test_anonymous_403_withholds_operator_guidance(app: Any) -> None:
    """The body names the rejected IP (the runbook reads it) but not the knob."""
    resp = _client(app, signed_in=False).delete(_CREDENTIALS)
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "203.0.113.9" in detail
    assert "ALFRED_TRUSTED_NETWORKS" not in detail
    assert "Tailscale" not in detail


def test_authenticated_403_keeps_operator_guidance(app: Any) -> None:
    """A signed-in operator on the wrong network still gets told how to fix it."""
    resp = _client(app, signed_in=True).delete(_CREDENTIALS)
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "203.0.113.9" in detail
    assert "ALFRED_TRUSTED_NETWORKS" in detail
    assert "203.0.113.9/24" in detail


@pytest.mark.parametrize(("method", "body"), DEVICE_ROUTES)
def test_device_routes_take_a_session_from_anywhere(
    app: Any, method: str, body: dict[str, str]
) -> None:
    """A phone off the LAN registers for push with its session alone (spec §6.2)."""
    resp = _client(app, signed_in=True).request(method, "/api/devices/register", json=body)
    assert resp.status_code == 200


@pytest.mark.parametrize("peer", [("192.168.1.20", 12345), _UNTRUSTED])
@pytest.mark.parametrize(("method", "body"), DEVICE_ROUTES)
def test_device_routes_still_need_a_session(
    app: Any, method: str, body: dict[str, str], peer: tuple[str, int]
) -> None:
    """Signed out is 401 from the LAN and from outside alike: no network gate answers."""
    resp = _client(app, signed_in=False, peer=peer).request(
        method, "/api/devices/register", json=body
    )
    assert resp.status_code == 401
