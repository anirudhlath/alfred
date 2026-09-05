"""The credential-equivalent routes are gated TWICE: trusted network AND passkey session.

The rest of the suite reaches these routes over TestClient's default peer, the literal
``"testclient"``, which ``require_trusted_network`` bypasses — so the network half of the
gate is never actually exercised there. These tests give TestClient a real, untrusted peer
address so both halves run, and pin the *order* the two gates run in.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.channels.web_server import create_app
from tests.core.channels.conftest import _TEST_SESSION_ID, make_session_redis

_UNTRUSTED = ("203.0.113.9", 12345)  # TEST-NET-3 — never RFC1918, CGNAT or loopback

# (method, path, json body) for every route carrying both gates.
DOUBLE_GATED: list[tuple[str, str, dict[str, str] | None]] = [
    ("PUT", "/api/integrations/home-service/credentials", {"url": "http://x", "token": "t"}),
    ("DELETE", "/api/integrations/home-service/credentials", None),
    (
        "POST",
        "/api/devices/register",
        {"device_token": "aabbccdd11223344aabbccdd11223344", "platform": "ios", "identity": "sir"},
    ),
    ("DELETE", "/api/devices/register", {"device_token": "aabbccdd11223344aabbccdd11223344"}),
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real app, with a Redis fake that recognises exactly one session."""
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS_STRICT", raising=False)

    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = make_session_redis()
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
    """Untrusted peer + a *valid* cookie → 403, never 401.

    Order is load-bearing: if the session gate ran first, the 401-vs-403 split would
    tell an anonymous internet caller whether a stolen cookie is still live.
    """
    resp = _client(app, signed_in=True).post(
        "/api/devices/register",
        json={
            "device_token": "aabbccdd11223344aabbccdd11223344",
            "platform": "ios",
            "identity": "sir",
        },
    )
    assert resp.status_code == 403
    assert resp.status_code != 401


def test_anonymous_403_withholds_operator_guidance(app: Any) -> None:
    """The body names the rejected IP (the runbook reads it) but not the knob."""
    resp = _client(app, signed_in=False).delete("/api/devices/register")
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "203.0.113.9" in detail
    assert "ALFRED_TRUSTED_NETWORKS" not in detail
    assert "Tailscale" not in detail


def test_authenticated_403_keeps_operator_guidance(app: Any) -> None:
    """A signed-in operator on the wrong network still gets told how to fix it."""
    resp = _client(app, signed_in=True).delete("/api/devices/register")
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "203.0.113.9" in detail
    assert "ALFRED_TRUSTED_NETWORKS" in detail
    assert "203.0.113.9/24" in detail
