from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_localhost_ipv4_allowed() -> None:
    from core.channels.web_server import require_trusted_network

    request = MagicMock()
    request.client.host = "127.0.0.1"
    await require_trusted_network(request)  # Should not raise


@pytest.mark.asyncio
async def test_localhost_ipv6_allowed() -> None:
    from core.channels.web_server import require_trusted_network

    request = MagicMock()
    request.client.host = "::1"
    await require_trusted_network(request)  # Should not raise


@pytest.mark.asyncio
async def test_tailscale_cgnat_allowed() -> None:
    from core.channels.web_server import require_trusted_network

    request = MagicMock()
    request.client.host = "100.100.50.25"
    await require_trusted_network(request)  # Should not raise


@pytest.mark.asyncio
async def test_tailscale_cgnat_edge_low() -> None:
    from core.channels.web_server import require_trusted_network

    request = MagicMock()
    request.client.host = "100.64.0.1"
    await require_trusted_network(request)  # Should not raise


@pytest.mark.asyncio
async def test_tailscale_cgnat_edge_high() -> None:
    from core.channels.web_server import require_trusted_network

    request = MagicMock()
    request.client.host = "100.127.255.254"
    await require_trusted_network(request)  # Should not raise


@pytest.mark.asyncio
async def test_external_ip_rejected() -> None:
    from core.channels.web_server import require_trusted_network

    request = MagicMock()
    request.client.host = "203.0.113.50"
    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_network(request)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_tailscale_100_range_rejected() -> None:
    """100.128.0.1 is outside the CGNAT /10 range."""
    from core.channels.web_server import require_trusted_network

    request = MagicMock()
    request.client.host = "100.128.0.1"
    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_network(request)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_testclient_allowed() -> None:
    """TestClient uses 'testclient' as host — must still be allowed for tests."""
    from core.channels.web_server import require_trusted_network

    request = MagicMock()
    request.client.host = "testclient"
    await require_trusted_network(request)  # Should not raise
