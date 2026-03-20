"""Tests for signal-cli subprocess integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.channels.signal_bridge.bridge import SignalBridge


@pytest.mark.asyncio
async def test_send_signal_calls_subprocess() -> None:
    """_send_signal should invoke signal-cli send via subprocess."""
    bridge = SignalBridge(redis=AsyncMock(), phone_number="+1234567890")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await bridge._send_signal("+1234567890", "Test message")

    mock_exec.assert_called_once()
    args = mock_exec.call_args[0]
    assert "signal-cli" in args
    assert "send" in args
    assert "+1234567890" in args
    assert "Test message" in args


@pytest.mark.asyncio
async def test_send_signal_handles_failure() -> None:
    """_send_signal should log warning on subprocess failure, not raise."""
    bridge = SignalBridge(redis=AsyncMock(), phone_number="+1234567890")

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        # Should not raise
        await bridge._send_signal("+1234567890", "Test")
