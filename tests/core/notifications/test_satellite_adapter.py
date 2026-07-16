"""SatelliteChannelAdapter — spoken URGENT announcements."""

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

    adapter = SatelliteChannelAdapter(get_bridge=lambda: bridge, get_tts=lambda: tts)
    await adapter.deliver(_notification())

    tts.synthesize.assert_called_once_with("Smoke: Kitchen smoke detected")
    bridge.play_wav_all.assert_awaited_once_with(b"RIFFwav")


async def test_supports_urgent_only() -> None:
    adapter = SatelliteChannelAdapter(get_bridge=lambda: None, get_tts=lambda: None)
    assert adapter.supports_urgency(Urgency.URGENT)
    assert not adapter.supports_urgency(Urgency.IMPORTANT)
    assert not adapter.supports_urgency(Urgency.INFORMATIONAL)


async def test_no_bridge_or_tts_is_noop() -> None:
    adapter = SatelliteChannelAdapter(get_bridge=lambda: None, get_tts=lambda: None)
    await adapter.deliver(_notification())  # must not raise
