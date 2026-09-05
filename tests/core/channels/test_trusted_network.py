from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from core.channels.web_server import require_trusted_network


def _gated_client(trusted_hosts: str) -> TestClient:
    """One gated route behind uvicorn's ProxyHeadersMiddleware, wired exactly as
    core.channels.__main__ runs it, so these tests exercise the real header chain.

    The route echoes the client the gate saw, so a test can tell "the header was
    applied" apart from "the header was ignored and the bypass let it through".
    """
    app = FastAPI()

    @app.get("/gated", dependencies=[Depends(require_trusted_network)])
    async def gated(request: Request) -> dict[str, str]:
        return {"host": request.client.host if request.client else ""}

    return TestClient(ProxyHeadersMiddleware(app, trusted_hosts=trusted_hosts))


@pytest.mark.asyncio
async def test_localhost_ipv4_allowed() -> None:
    request = MagicMock()
    request.client.host = "127.0.0.1"
    await require_trusted_network(request)


@pytest.mark.asyncio
async def test_localhost_ipv6_allowed() -> None:
    request = MagicMock()
    request.client.host = "::1"
    await require_trusted_network(request)


@pytest.mark.asyncio
async def test_tailscale_cgnat_allowed() -> None:
    request = MagicMock()
    request.client.host = "100.100.50.25"
    await require_trusted_network(request)


@pytest.mark.asyncio
async def test_tailscale_cgnat_edge_low() -> None:
    request = MagicMock()
    request.client.host = "100.64.0.1"
    await require_trusted_network(request)


@pytest.mark.asyncio
async def test_tailscale_cgnat_edge_high() -> None:
    request = MagicMock()
    request.client.host = "100.127.255.254"
    await require_trusted_network(request)


@pytest.mark.asyncio
async def test_external_ip_rejected() -> None:
    request = MagicMock()
    request.client.host = "203.0.113.50"
    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_network(request)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_tailscale_100_range_rejected() -> None:
    """100.128.0.1 is outside the CGNAT /10 range."""
    request = MagicMock()
    request.client.host = "100.128.0.1"
    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_network(request)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_testclient_allowed() -> None:
    """TestClient uses 'testclient' as host — must still be allowed for tests."""
    request = MagicMock()
    request.client.host = "testclient"
    await require_trusted_network(request)


@pytest.mark.asyncio
async def test_no_client_rejected() -> None:
    """If request.client is None, access should be denied."""
    request = MagicMock()
    request.client = None
    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_network(request)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_private_lan_allowed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """RFC1918 LAN (Docker bridge, home network) is trusted by default — the common case."""
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS_STRICT", raising=False)
    for host in ("172.17.0.1", "192.168.1.50", "10.4.5.6"):
        request = MagicMock()
        request.client.host = host
        await require_trusted_network(request)  # no raise


@pytest.mark.asyncio
async def test_private_lan_blocked_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """STRICT mode drops the RFC1918 defaults — only loopback/Tailscale/explicit remain."""
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    monkeypatch.setenv("ALFRED_TRUSTED_NETWORKS_STRICT", "1")
    request = MagicMock()
    request.client.host = "192.168.1.50"
    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_network(request)
    assert exc_info.value.status_code == 403
    # loopback + Tailscale still trusted under strict mode
    for host in ("127.0.0.1", "100.100.50.25"):
        ok = MagicMock()
        ok.client.host = host
        await require_trusted_network(ok)


@pytest.mark.asyncio
async def test_403_detail_names_client_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 403 names the rejected IP even for an anonymous caller — the deploy runbook
    has the operator read the observed peer out of this body — but nothing else.

    `request.state.authenticated` is set explicitly: a bare MagicMock attribute is
    truthy, which would silently exercise the authenticated branch instead.
    """
    monkeypatch.setenv("ALFRED_TRUSTED_NETWORKS_STRICT", "1")
    request = MagicMock()
    request.client.host = "203.0.113.99"
    request.state.authenticated = False
    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_network(request)
    assert exc_info.value.detail == (
        "Access restricted to trusted networks: 203.0.113.99 is not trusted."
    )


@pytest.mark.asyncio
async def test_403_detail_adds_guidance_for_authenticated_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signed-in operator on the wrong network gets told how to fix it; an anonymous
    internet caller does not, because the knob names describe the perimeter."""
    monkeypatch.setenv("ALFRED_TRUSTED_NETWORKS_STRICT", "1")
    request = MagicMock()
    request.client.host = "203.0.113.99"
    request.state.authenticated = True
    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_network(request)
    detail = exc_info.value.detail
    assert "203.0.113.99" in detail
    assert "ALFRED_TRUSTED_NETWORKS" in detail
    assert "Tailscale" in detail


@pytest.mark.asyncio
async def test_container_subnet_allowed_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_TRUSTED_NETWORKS", "172.16.0.0/12,192.168.64.0/24")
    request = MagicMock()
    request.client.host = "172.17.0.1"
    await require_trusted_network(request)  # docker bridge — no raise

    request.client.host = "192.168.64.5"
    await require_trusted_network(request)  # apple container vmnet — no raise


def test_forwarded_for_from_trusted_proxy_reaches_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through uvicorn's ProxyHeadersMiddleware the gate sees the *forwarded* client,
    not the proxy — so a public caller behind NPM is rejected by IP, and the 403
    names that IP (the operator uses it to pick FORWARDED_ALLOW_IPS)."""
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS_STRICT", raising=False)

    # TestClient's peer is the literal "testclient"; trust it as the proxy.
    client = _gated_client("testclient")

    public = client.get("/gated", headers={"X-Forwarded-For": "203.0.113.9"})
    assert public.status_code == 403
    assert "203.0.113.9" in public.json()["detail"]

    lan = client.get("/gated", headers={"X-Forwarded-For": "192.168.1.20"})
    assert lan.status_code == 200
    assert lan.json()["host"] == "192.168.1.20"


def test_forwarded_for_uses_rightmost_untrusted_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a chain of proxies uvicorn walks right-to-left and stops at the first
    hop it does not trust — NOT the leftmost, which any client can forge. Pinned
    here so a uvicorn bump cannot change the gate's notion of "the client" in silence."""
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS_STRICT", raising=False)

    client = _gated_client("testclient,172.18.0.5,127.0.0.1")
    resp = client.get(
        "/gated",
        headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7, 172.18.0.5"},
    )

    # 172.18.0.5 is trusted so it is skipped; 198.51.100.7 is the first untrusted
    # hop from the right. The forged-looking 203.0.113.9 on the left is ignored.
    assert resp.status_code == 403
    assert "198.51.100.7" in resp.json()["detail"]
    assert "203.0.113.9" not in resp.json()["detail"]


def test_forwarded_for_from_untrusted_peer_is_ignored() -> None:
    """A peer that is not in FORWARDED_ALLOW_IPS cannot spoof its way in or out."""
    client = _gated_client("10.9.9.9")
    resp = client.get("/gated", headers={"X-Forwarded-For": "203.0.113.9"})

    # Header ignored → peer stays "testclient" → the test bypass applies → 200.
    assert resp.status_code == 200
    assert resp.json()["host"] == "testclient"
    assert resp.json()["host"] != "203.0.113.9"
