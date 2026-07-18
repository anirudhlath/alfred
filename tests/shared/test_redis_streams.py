"""Tests for shared.redis_streams — create_redis() socket_timeout policy."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from shared.redis_streams import create_redis


def test_create_redis_passes_socket_timeout_none() -> None:
    """redis-py 8 defaults socket_timeout to 5s, which breaks idle blocking stream
    reads (block=). create_redis must always pass socket_timeout=None so block=
    alone governs read timeouts.
    """
    mock_client = MagicMock()
    with patch("redis.asyncio.from_url", return_value=mock_client) as mock_from_url:
        result = create_redis("redis://localhost:6379")

    mock_from_url.assert_called_once_with(
        "redis://localhost:6379", decode_responses=False, socket_timeout=None
    )
    assert result is mock_client


def test_create_redis_forwards_decode_responses() -> None:
    """decode_responses must be forwarded verbatim (e.g. web_server.py needs False)."""
    mock_client = MagicMock()
    with patch("redis.asyncio.from_url", return_value=mock_client) as mock_from_url:
        create_redis("redis://localhost:6379", decode_responses=True)

    mock_from_url.assert_called_once_with(
        "redis://localhost:6379", decode_responses=True, socket_timeout=None
    )
