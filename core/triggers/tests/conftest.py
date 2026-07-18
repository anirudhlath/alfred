"""Conftest for trigger tests — ensures trigger type modules can be re-imported per test."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_TRIGGER_TYPE_MODULES = [
    "core.triggers.types",
    "core.triggers.types.time",
    "core.triggers.types.sensor",
    "core.triggers.types.composite",
]


@pytest.fixture(autouse=True)
def _reset_trigger_type_modules() -> None:
    """Remove trigger type modules from sys.modules so fixture imports re-execute decorators."""
    for mod in _TRIGGER_TYPE_MODULES:
        sys.modules.pop(mod, None)


class FakePubSub:
    """Minimal async pub/sub compatible with redis.asyncio's PubSub surface."""

    def __init__(self, hub: FakeRedis) -> None:
        self._hub = hub
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self._hub.subscribers.setdefault(channel, []).append(self._queue)

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self._queue.get()

    async def aclose(self) -> None:
        for queues in self._hub.subscribers.values():
            with contextlib.suppress(ValueError):
                queues.remove(self._queue)


class FakeRedis:
    """In-memory Redis stub: hash + kv + streams + pub/sub broadcast."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}
        self.streams: dict[str, list[dict[str, str]]] = {}
        self.lists: dict[str, list[str]] = {}
        self.subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self)

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, field: str) -> None:
        self.hashes.get(key, {}).pop(field, None)

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str) -> None:
        self.kv[key] = value

    async def xadd(self, stream: str, fields: dict[str, str]) -> None:
        self.streams.setdefault(stream, []).append(fields)

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def publish(self, channel: str, message: str) -> None:
        for q in self.subscribers.get(channel, []):
            q.put_nowait({"type": "message", "channel": channel, "data": message})


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()
