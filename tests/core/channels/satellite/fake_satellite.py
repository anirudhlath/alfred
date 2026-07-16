"""In-process fake wyoming-satellite for bridge tests."""

from __future__ import annotations

import asyncio

from wyoming.event import Event, async_read_event, async_write_event
from wyoming.info import Attribution, Describe, Info, Satellite


class FakeSatellite:
    """Accepts one connection; records received events; scripts satellite events."""

    def __init__(self) -> None:
        self.received: list[Event] = []
        self.port = 0
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = asyncio.Event()
        self.run_satellite_seen = asyncio.Event()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_conn, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def wait_connected(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def send(self, event: Event) -> None:
        assert self._writer is not None
        await async_write_event(event, self._writer)

    async def wait_for(self, event_type: str, timeout: float = 5.0) -> Event:
        """Wait until an event of the given type has been received; return it."""

        async def _poll() -> Event:
            while True:
                for ev in self.received:
                    if ev.type == event_type:
                        return ev
                await asyncio.sleep(0.01)

        return await asyncio.wait_for(_poll(), timeout)

    async def _on_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._connected.set()
        while (event := await async_read_event(reader)) is not None:
            self.received.append(event)
            if Describe.is_type(event.type):
                info = Info(
                    satellite=Satellite(
                        name="fake-sat",
                        attribution=Attribution(name="test", url=""),
                        installed=True,
                        description="fake",
                        version="1.0",
                    )
                )
                await async_write_event(info.event(), writer)
            elif event.type == "run-satellite":
                self.run_satellite_seen.set()
