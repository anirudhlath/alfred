"""SatelliteBridge ↔ fake satellite integration tests."""

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from wyoming.audio import AudioChunk
from wyoming.pipeline import PipelineStage, RunPipeline

from core.channels.satellite import bridge as bridge_module
from core.channels.satellite.audio import pcm_to_wav
from core.channels.satellite.bridge import SatelliteBridge, SatelliteConnection
from core.channels.satellite.config import SatelliteEntry
from core.channels.satellite.endpointing import UtteranceCollector

from .fake_satellite import FakeSatellite

FRAME = b"\x01\x00" * 512


def _scripted_collector(probs: list[float]) -> UtteranceCollector:
    q = deque(probs)
    return UtteranceCollector(vad=lambda _f: q.popleft() if q else 0.0)


@pytest.fixture
async def fake_sat() -> AsyncGenerator[FakeSatellite]:
    sat = FakeSatellite()
    await sat.start()
    yield sat
    await sat.stop()


def _entry(sat: FakeSatellite) -> SatelliteEntry:
    return SatelliteEntry(name="kitchen", host="127.0.0.1", port=sat.port, area="Kitchen")


async def test_handshake_and_arm(fake_sat: FakeSatellite) -> None:
    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        assert [e.type for e in fake_sat.received][:2] == ["describe", "run-satellite"]
        assert len(bridge.connections()) == 1
    finally:
        await bridge.stop()


async def test_wake_utterance_flow_invokes_handler(fake_sat: FakeSatellite) -> None:
    got: asyncio.Future[tuple[SatelliteConnection, bytes]] = (
        asyncio.get_event_loop().create_future()
    )

    async def handler(conn: SatelliteConnection, pcm: bytes) -> None:
        got.set_result((conn, pcm))
        await conn.send_transcript("turn off the lights")

    # 5 speech frames then silence until the 800ms end fires
    bridge = SatelliteBridge(
        [_entry(fake_sat)],
        handler=handler,
        collector_factory=lambda: _scripted_collector([0.9] * 5 + [0.0] * 100),
    )
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        await fake_sat.send(
            RunPipeline(start_stage=PipelineStage.ASR, end_stage=PipelineStage.TTS).event()
        )
        for _ in range(40):
            await fake_sat.send(AudioChunk(rate=16000, width=2, channels=1, audio=FRAME).event())
        conn, pcm = await asyncio.wait_for(got, 5.0)
        assert conn.entry.name == "kitchen"
        assert len(pcm) >= 5 * len(FRAME)
        await fake_sat.wait_for("voice-started")
        await fake_sat.wait_for("voice-stopped")
        transcript = await fake_sat.wait_for("transcript")
        assert transcript.data["text"] == "turn off the lights"
    finally:
        await bridge.stop()


async def test_play_wav_streams_audio(fake_sat: FakeSatellite) -> None:
    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        wav = pcm_to_wav(b"\x00\x01" * 4096, rate=22050)
        delivered = await bridge.play_wav_all(wav)
        assert delivered == 1
        start = await fake_sat.wait_for("audio-start")
        assert start.data["rate"] == 22050
        await fake_sat.wait_for("audio-stop")
    finally:
        await bridge.stop()


async def test_reconnects_after_disconnect(fake_sat: FakeSatellite) -> None:
    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        fake_sat.run_satellite_seen.clear()
        fake_sat.received.clear()
        assert fake_sat._writer is not None
        fake_sat._writer.close()  # drop the connection
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 10.0)  # re-handshake
    finally:
        await bridge.stop()


async def test_ping_answered_with_pong(fake_sat: FakeSatellite) -> None:
    from wyoming.ping import Ping

    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        await fake_sat.send(Ping(text="x").event())
        pong = await fake_sat.wait_for("pong")
        assert pong.data.get("text") == "x"
    finally:
        await bridge.stop()


async def test_backoff_resets_after_successful_reconnect(
    fake_sat: FakeSatellite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session that reached RunSatellite resets the retry ladder back to 1.0s."""
    delays: list[float] = []
    real_sleep = asyncio.sleep

    class _AsyncioProxy:
        """asyncio as seen by the bridge module, with sleep() recorded and fast-forwarded."""

        def __getattr__(self, name: str) -> Any:
            return getattr(asyncio, name)

        async def sleep(self, delay: float) -> None:
            delays.append(delay)
            if delay >= bridge_module._PING_INTERVAL_S:
                await asyncio.Event().wait()  # park the ping loop until cancelled
            else:
                await real_sleep(0)  # fast-forward reconnect backoff

    monkeypatch.setattr(bridge_module, "asyncio", _AsyncioProxy())

    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    try:
        for _ in range(2):  # two successful sessions, each dropped after RunSatellite
            await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
            fake_sat.run_satellite_seen.clear()
            assert fake_sat._writer is not None
            fake_sat._writer.close()  # drop the connection
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)  # third session
        retry_delays = [d for d in delays if d < bridge_module._PING_INTERVAL_S]
        # Both retries follow a session that reached RunSatellite → both start at 1.0s.
        assert retry_delays[:2] == [1.0, 1.0]
    finally:
        await bridge.stop()


async def test_stop_sends_pause_satellite(fake_sat: FakeSatellite) -> None:
    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
    await bridge.stop()
    await fake_sat.wait_for("pause-satellite")


async def test_stop_completes_when_ping_task_already_dead(
    fake_sat: FakeSatellite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ping task that died before teardown must not swallow shutdown cancellation."""

    async def broken_ping_loop(self: SatelliteConnection) -> None:
        raise ConnectionResetError("broken pipe")  # half-open connection: ping write failed

    monkeypatch.setattr(SatelliteConnection, "_ping_loop", broken_ping_loop)

    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
    await asyncio.wait_for(bridge.stop(), 5.0)  # must not hang
