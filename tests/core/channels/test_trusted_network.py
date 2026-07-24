from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from core.channels.web_server import require_trusted_network


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
async def test_container_gateway_blocked_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    request = MagicMock()
    request.client.host = "172.17.0.1"
    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_network(request)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_container_subnet_allowed_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_TRUSTED_NETWORKS", "172.16.0.0/12,192.168.64.0/24")
    request = MagicMock()
    request.client.host = "172.17.0.1"
    await require_trusted_network(request)  # docker bridge — no raise

    request.client.host = "192.168.64.5"
    await require_trusted_network(request)  # apple container vmnet — no raise
