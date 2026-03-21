"""Tests for notification delivery worker — local adapter delivery."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.delivery import _deliver_locally
from core.notifications.schema import Notification, Urgency


class FakeSignal(ChannelAdapter):
    name: ClassVar[str] = "fake_signal"
    supported_urgencies: ClassVar[set[Urgency]] = {
        Urgency.INFORMATIONAL,
        Urgency.IMPORTANT,
        Urgency.URGENT,
    }

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


class FakeWS(ChannelAdapter):
    name: ClassVar[str] = "fake_ws"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.IMPORTANT, Urgency.URGENT}

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


class FakeVoice(ChannelAdapter):
    name: ClassVar[str] = "fake_voice"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.URGENT}

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    ChannelRegistry.reset()


def _make_notification(urgency: Urgency = Urgency.INFORMATIONAL) -> Notification:
    return Notification(title="Test", body="Hello", urgency=urgency, source="test")


def _register_all() -> tuple[FakeSignal, FakeWS, FakeVoice]:
    signal = FakeSignal()
    ws = FakeWS()
    voice = FakeVoice()
    ChannelRegistry._registry["fake_signal"] = FakeSignal
    ChannelRegistry._instances["fake_signal"] = signal
    ChannelRegistry._registry["fake_ws"] = FakeWS
    ChannelRegistry._instances["fake_ws"] = ws
    ChannelRegistry._registry["fake_voice"] = FakeVoice
    ChannelRegistry._instances["fake_voice"] = voice
    return signal, ws, voice


class TestDeliverLocally:
    @pytest.mark.asyncio
    async def test_informational_to_signal_only(self) -> None:
        signal, ws, voice = _register_all()
        await _deliver_locally(_make_notification(Urgency.INFORMATIONAL))
        assert len(signal.delivered) == 1
        assert len(ws.delivered) == 0
        assert len(voice.delivered) == 0

    @pytest.mark.asyncio
    async def test_important_to_signal_and_ws(self) -> None:
        signal, ws, voice = _register_all()
        await _deliver_locally(_make_notification(Urgency.IMPORTANT))
        assert len(signal.delivered) == 1
        assert len(ws.delivered) == 1
        assert len(voice.delivered) == 0

    @pytest.mark.asyncio
    async def test_urgent_to_all_channels(self) -> None:
        signal, ws, voice = _register_all()
        await _deliver_locally(_make_notification(Urgency.URGENT))
        assert len(signal.delivered) == 1
        assert len(ws.delivered) == 1
        assert len(voice.delivered) == 1

    @pytest.mark.asyncio
    async def test_no_adapters_is_silent(self) -> None:
        """No error when no adapters are registered for an urgency."""
        await _deliver_locally(_make_notification(Urgency.INFORMATIONAL))

    @pytest.mark.asyncio
    async def test_adapter_error_doesnt_block_others(self) -> None:
        signal, ws, voice = _register_all()
        ws.deliver = AsyncMock(side_effect=RuntimeError("WS down"))  # type: ignore[method-assign]
        await _deliver_locally(_make_notification(Urgency.URGENT))
        # Signal and voice still delivered despite WS failure
        assert len(signal.delivered) == 1
        assert len(voice.delivered) == 1
