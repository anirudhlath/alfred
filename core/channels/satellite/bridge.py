"""Wyoming bridge — persistent connections from Alfred to each satellite.

Wyoming inverts the usual direction: satellites LISTEN on :10700 and this
bridge connects out to them. See docs/voice-satellites.md for the event flow.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from loguru import logger
from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.error import Error
from wyoming.info import Describe, Info
from wyoming.ping import Ping, Pong
from wyoming.pipeline import RunPipeline
from wyoming.satellite import PauseSatellite, RunSatellite
from wyoming.tts import Synthesize
from wyoming.vad import VoiceStarted, VoiceStopped

from core.channels.satellite.audio import wav_to_pcm
from core.channels.satellite.endpointing import (
    CollectorEvent,
    UtteranceCollector,
    default_collector_factory,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from wyoming.event import Event

    from core.channels.satellite.config import SatelliteEntry

    UtteranceHandler = Callable[["SatelliteConnection", bytes], Awaitable[None]]

_SAMPLES_PER_CHUNK = 1024
_PING_INTERVAL_S = 10.0
_READ_TIMEOUT_S = 30.0


class SatelliteConnection:
    """One satellite: connect/handshake/event loop with reconnect."""

    def __init__(
        self,
        entry: SatelliteEntry,
        handler: UtteranceHandler,
        collector_factory: Callable[[], UtteranceCollector],
        reconnect_max_s: float,
    ) -> None:
        self.entry = entry
        self._handler = handler
        self._collector_factory = collector_factory
        self._reconnect_max_s = reconnect_max_s
        self._client: AsyncTcpClient | None = None
        self._connected = False
        self._send_lock = asyncio.Lock()
        self._audio_lock = asyncio.Lock()
        self._collector: UtteranceCollector | None = None
        self._converter = AudioChunkConverter(rate=16000, width=2, channels=1)
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def connected(self) -> bool:
        return self._connected

    async def run(self) -> None:
        """Reconnect-forever loop. Cancelled on bridge shutdown."""
        backoff = 1.0
        while True:
            try:
                await self._run_once()
                backoff = 1.0
            except asyncio.CancelledError:
                await self._graceful_close()
                raise
            except Exception as exc:
                logger.warning(
                    "Satellite '{}' connection lost ({}: {}) — retrying in {:.0f}s",
                    self.entry.name,
                    type(exc).__name__,
                    exc,
                    backoff,
                )
            self._connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._reconnect_max_s)

    async def _run_once(self) -> None:
        client = AsyncTcpClient(self.entry.host, self.entry.port)
        await client.connect()
        self._client = client
        try:
            await self._send(Describe().event())
            while True:
                event = await asyncio.wait_for(client.read_event(), _READ_TIMEOUT_S)
                if event is None:
                    raise ConnectionResetError("satellite closed connection")
                if Info.is_type(event.type):
                    break
            await self._send(RunSatellite().event())
            self._connected = True
            logger.info("Satellite '{}' connected ({})", self.entry.name, self.entry.host)

            ping_task = asyncio.create_task(self._ping_loop())
            try:
                while True:
                    event = await asyncio.wait_for(client.read_event(), _READ_TIMEOUT_S)
                    if event is None:
                        raise ConnectionResetError("satellite closed connection")
                    await self._handle_event(event)
            finally:
                ping_task.cancel()
        finally:
            self._connected = False
            await client.disconnect()
            self._client = None

    async def _handle_event(self, event: Event) -> None:
        if AudioChunk.is_type(event.type):
            if self._collector is None:
                return  # trailing audio after utterance end / no active run
            chunk = self._converter.convert(AudioChunk.from_event(event))
            for coll_event in self._collector.feed(chunk.audio):
                await self._on_collector_event(coll_event)
        elif RunPipeline.is_type(event.type):
            logger.debug("Satellite '{}': pipeline run started", self.entry.name)
            self._collector = self._collector_factory()
        elif Ping.is_type(event.type):
            await self._send(Pong(text=Ping.from_event(event).text).event())
        elif event.type in ("detection", "played", "pong", "voice-started", "voice-stopped"):
            logger.debug("Satellite '{}': {}", self.entry.name, event.type)
        else:
            logger.debug("Satellite '{}': ignoring event {}", self.entry.name, event.type)

    async def _on_collector_event(self, coll_event: CollectorEvent) -> None:
        if coll_event.kind == "speech_start":
            await self._send(VoiceStarted().event())
        elif coll_event.kind == "utterance":
            await self._send(VoiceStopped().event())
            self._collector = None
            assert coll_event.pcm is not None
            task = asyncio.create_task(self._run_handler(coll_event.pcm))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif coll_event.kind == "timeout":
            logger.debug("Satellite '{}': no speech — re-arming", self.entry.name)
            self._collector = None
            await self.send_transcript("")

    async def _run_handler(self, pcm: bytes) -> None:
        try:
            await self._handler(self, pcm)
        except Exception as exc:
            logger.error("Satellite '{}' pipeline failed: {}", self.entry.name, exc)
            await self.send_error(str(exc))

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(_PING_INTERVAL_S)
            await self._send(Ping().event())

    async def _send(self, event: Event) -> None:
        if self._client is None:
            raise ConnectionResetError("not connected")
        async with self._send_lock:
            await self._client.write_event(event)

    async def _graceful_close(self) -> None:
        with contextlib.suppress(Exception):  # best-effort during shutdown
            if self._client is not None:
                await self._send(PauseSatellite().event())

    # -- public send API (used by the pipeline handler + announcements) --

    async def send_transcript(self, text: str) -> None:
        """End-of-command signal: satellite stops streaming and re-arms."""
        await self._send(Transcript(text=text).event())

    async def send_synthesize(self, text: str) -> None:
        """FYI event before reply audio (satellite may show/log it)."""
        await self._send(Synthesize(text=text).event())

    async def send_error(self, text: str) -> None:
        """Error event — satellite stops streaming and plays error feedback."""
        with contextlib.suppress(Exception):  # connection may already be gone
            await self._send(Error(text=text).event())

    async def play_wav(self, wav_bytes: bytes) -> None:
        """Stream a WAV to the satellite speaker (reply or announcement)."""
        pcm, rate, width, channels = wav_to_pcm(wav_bytes)
        bytes_per_chunk = _SAMPLES_PER_CHUNK * width * channels
        async with self._audio_lock:
            await self._send(AudioStart(rate=rate, width=width, channels=channels).event())
            timestamp = 0
            for i in range(0, len(pcm), bytes_per_chunk):
                chunk = pcm[i : i + bytes_per_chunk]
                await self._send(
                    AudioChunk(
                        rate=rate,
                        width=width,
                        channels=channels,
                        audio=chunk,
                        timestamp=timestamp,
                    ).event()
                )
                timestamp += (len(chunk) // (width * channels)) * 1000 // rate
            await self._send(AudioStop(timestamp=timestamp).event())


class SatelliteBridge:
    """Owns one SatelliteConnection task per configured satellite."""

    def __init__(
        self,
        entries: list[SatelliteEntry],
        handler: UtteranceHandler,
        *,
        collector_factory: Callable[[], UtteranceCollector] = default_collector_factory,
        reconnect_max_s: float = 60.0,
    ) -> None:
        self._connections = [
            SatelliteConnection(entry, handler, collector_factory, reconnect_max_s)
            for entry in entries
        ]
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        """Spawn one supervisor task per satellite."""
        self._tasks = [asyncio.create_task(conn.run()) for conn in self._connections]
        logger.info("Satellite bridge started ({} satellites)", len(self._connections))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def connections(self) -> list[SatelliteConnection]:
        """Currently-connected satellites."""
        return [c for c in self._connections if c.connected]

    async def play_wav_all(self, wav: bytes) -> int:
        """Play a WAV on every online satellite. Returns delivery count."""
        delivered = 0
        for conn in self.connections():
            try:
                await conn.play_wav(wav)
                delivered += 1
            except Exception as exc:
                logger.warning("Announcement to satellite '{}' failed: {}", conn.entry.name, exc)
        return delivered
