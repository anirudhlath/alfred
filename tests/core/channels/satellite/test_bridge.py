"""SatelliteBridge ↔ fake satellite integration tests."""

import asyncio
from collections import deque
from collections.abc import AsyncGenerator

import pytest
from wyoming.audio import AudioChunk
from wyoming.pipeline import PipelineStage, RunPipeline

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
