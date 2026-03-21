"""Tests for concrete channel adapters: Signal, WebSocket, Voice."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.notifications.channels import ChannelRegistry
from core.notifications.schema import Notification, Urgency


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    ChannelRegistry.reset()


def _make_notification(urgency: Urgency = Urgency.IMPORTANT) -> Notification:
    return Notification(title="Test", body="Hello world", urgency=urgency, source="test")


class TestSignalChannelAdapter:
    @pytest.mark.asyncio
    async def test_delivers_formatted_message(self) -> None:
        from core.notifications.adapters.signal import SignalChannelAdapter

        bridge = AsyncMock()
        adapter = SignalChannelAdapter(bridge=bridge, recipient="+15551234567")

        notification = _make_notification()
        await adapter.deliver(notification)

        bridge.send_notification.assert_called_once_with("+15551234567", "Test: Hello world")

    def test_supports_all_urgencies(self) -> None:
        from core.notifications.adapters.signal import SignalChannelAdapter

        adapter = SignalChannelAdapter(bridge=AsyncMock(), recipient="+15551234567")
        assert adapter.supports_urgency(Urgency.INFORMATIONAL)
        assert adapter.supports_urgency(Urgency.IMPORTANT)
        assert adapter.supports_urgency(Urgency.URGENT)


class TestWebSocketChannelAdapter:
    @pytest.mark.asyncio
    async def test_delivers_to_connected_sessions(self) -> None:
        from core.notifications.adapters.websocket import WebSocketChannelAdapter

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        session_getter = MagicMock(return_value=[ws1, ws2])

        adapter = WebSocketChannelAdapter(get_sessions=session_getter)
        await adapter.deliver(_make_notification())

        ws1.send_json.assert_called_once()
        ws2.send_json.assert_called_once()
        payload = ws1.send_json.call_args[0][0]
        assert payload["type"] == "notification"
        assert payload["title"] == "Test"

    @pytest.mark.asyncio
    async def test_silently_skips_when_no_sessions(self) -> None:
        from core.notifications.adapters.websocket import WebSocketChannelAdapter

        session_getter = MagicMock(return_value=[])
        adapter = WebSocketChannelAdapter(get_sessions=session_getter)
        # Should not raise
        await adapter.deliver(_make_notification())

    @pytest.mark.asyncio
    async def test_handles_send_failure_gracefully(self) -> None:
        from core.notifications.adapters.websocket import WebSocketChannelAdapter

        ws = AsyncMock()
        ws.send_json.side_effect = Exception("Connection closed")
        session_getter = MagicMock(return_value=[ws])

        adapter = WebSocketChannelAdapter(get_sessions=session_getter)
        # Should not raise — errors are logged
        await adapter.deliver(_make_notification())

    def test_supports_important_and_urgent(self) -> None:
        from core.notifications.adapters.websocket import WebSocketChannelAdapter

        adapter = WebSocketChannelAdapter(get_sessions=MagicMock(return_value=[]))
        assert not adapter.supports_urgency(Urgency.INFORMATIONAL)
        assert adapter.supports_urgency(Urgency.IMPORTANT)
        assert adapter.supports_urgency(Urgency.URGENT)


class TestVoiceChannelAdapter:
    @pytest.mark.asyncio
    async def test_synthesizes_and_pushes_audio(self) -> None:
        from core.notifications.adapters.voice import VoiceChannelAdapter

        tts = MagicMock()
        tts.synthesize.return_value = b"\x00\x01\x02\x03"  # Fake WAV bytes
        ws = AsyncMock()
        session_getter = MagicMock(return_value=[ws])

        adapter = VoiceChannelAdapter(get_tts=lambda: tts, get_sessions=session_getter)
        await adapter.deliver(_make_notification(Urgency.URGENT))

        tts.synthesize.assert_called_once_with("Test: Hello world")
        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "voice_notification"
        assert payload["audio"] == base64.b64encode(b"\x00\x01\x02\x03").decode()

    @pytest.mark.asyncio
    async def test_skips_when_tts_unavailable(self) -> None:
        from core.notifications.adapters.voice import VoiceChannelAdapter

        adapter = VoiceChannelAdapter(get_tts=lambda: None, get_sessions=MagicMock(return_value=[]))
        # Should not raise
        await adapter.deliver(_make_notification(Urgency.URGENT))

    @pytest.mark.asyncio
    async def test_skips_when_no_sessions(self) -> None:
        from core.notifications.adapters.voice import VoiceChannelAdapter

        tts = MagicMock()
        adapter = VoiceChannelAdapter(get_tts=lambda: tts, get_sessions=MagicMock(return_value=[]))
        await adapter.deliver(_make_notification(Urgency.URGENT))
        tts.synthesize.assert_not_called()

    def test_supports_urgent_only(self) -> None:
        from core.notifications.adapters.voice import VoiceChannelAdapter

        adapter = VoiceChannelAdapter(get_tts=lambda: None, get_sessions=MagicMock(return_value=[]))
        assert not adapter.supports_urgency(Urgency.INFORMATIONAL)
        assert not adapter.supports_urgency(Urgency.IMPORTANT)
        assert adapter.supports_urgency(Urgency.URGENT)
