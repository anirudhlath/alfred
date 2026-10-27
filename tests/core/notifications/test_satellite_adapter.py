"""SatelliteChannelAdapter — spoken URGENT announcements."""

import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from core.notifications.adapters.satellite import SatelliteChannelAdapter
from core.notifications.schema import Notification, Urgency


def _notification(urgency: Urgency = Urgency.URGENT) -> Notification:
    return Notification(title="Smoke", body="Kitchen smoke detected", urgency=urgency, source="t")


async def test_urgent_is_synthesized_and_played_everywhere() -> None:
    bridge = AsyncMock()
    bridge.play_wav_all = AsyncMock(return_value=2)
    tts = MagicMock()
    tts.synthesize = MagicMock(return_value=b"RIFFwav")

    adapter = SatelliteChannelAdapter(
        get_bridge=lambda: bridge, get_tts=AsyncMock(return_value=tts)
    )
    await adapter.deliver(_notification())

    tts.synthesize.assert_called_once_with("Smoke: Kitchen smoke detected")
    bridge.play_wav_all.assert_awaited_once_with(b"RIFFwav")


async def test_synthesis_runs_off_event_loop() -> None:
    """Synthesis takes seconds; on the loop it would stall the whole channels
    process (issue #313)."""
    synth_threads: list[int] = []

    def synthesize(text: str) -> bytes:
        synth_threads.append(threading.get_ident())
        return b"RIFFwav"

    bridge = AsyncMock()
    bridge.play_wav_all = AsyncMock(return_value=1)
    tts = MagicMock()
    tts.synthesize = MagicMock(side_effect=synthesize)

    adapter = SatelliteChannelAdapter(
        get_bridge=lambda: bridge, get_tts=AsyncMock(return_value=tts)
    )
    await adapter.deliver(_notification())

    assert synth_threads
    assert synth_threads[0] != threading.get_ident()


async def test_get_tts_is_awaited_not_called_synchronously() -> None:
    """get_tts must be an async getter — a sync callable (the old contract)
    must fail loudly rather than silently stall the event loop on a cold load."""
    bridge = AsyncMock()
    bridge.play_wav_all = AsyncMock(return_value=1)
    tts = MagicMock()
    tts.synthesize = MagicMock(return_value=b"RIFFwav")

    async def aget_tts() -> Any:
        return tts

    adapter = SatelliteChannelAdapter(get_bridge=lambda: bridge, get_tts=aget_tts)
    await adapter.deliver(_notification())

    tts.synthesize.assert_called_once_with("Smoke: Kitchen smoke detected")


async def test_supports_urgent_only() -> None:
    adapter = SatelliteChannelAdapter(get_bridge=lambda: None, get_tts=AsyncMock(return_value=None))
    assert adapter.supports_urgency(Urgency.URGENT)
    assert not adapter.supports_urgency(Urgency.IMPORTANT)
    assert not adapter.supports_urgency(Urgency.INFORMATIONAL)


async def test_no_bridge_or_tts_is_noop() -> None:
    adapter = SatelliteChannelAdapter(get_bridge=lambda: None, get_tts=AsyncMock(return_value=None))
    await adapter.deliver(_notification())  # must not raise
