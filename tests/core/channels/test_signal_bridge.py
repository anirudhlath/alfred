"""Tests for Signal bridge forwarding."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.channels.signal_bridge.bridge import SignalBridge
from shared.streams import USER_REQUESTS_STREAM


@pytest.mark.asyncio
async def test_forward_inbound_to_redis() -> None:
    redis = AsyncMock()
    bridge = SignalBridge(redis=redis, phone_number="+1234567890")
    await bridge.forward_inbound(
        sender="+1234567890",
        message="Turn on the lights",
        timestamp="2026-03-19T10:00:00Z",
    )
    redis.xadd.assert_called_once()
    call_args = redis.xadd.call_args
    assert call_args[0][0] == USER_REQUESTS_STREAM


@pytest.mark.asyncio
async def test_forward_outbound_notification() -> None:
    redis = AsyncMock()
    signal_send = AsyncMock()
    bridge = SignalBridge(redis=redis, phone_number="+1234567890")
    bridge._send_signal = signal_send  # type: ignore[assignment]
    await bridge.send_notification(
        recipient="+1234567890",
        message="Daily budget exceeded",
    )
    signal_send.assert_called_once()
