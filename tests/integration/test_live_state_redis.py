"""Live state against a real Redis, in a throwaway container.

Opt-in (``ALFRED_MEMORY_EVAL_DOCKER=1``): atomicity and write order are properties of the
server and the client together, which a fake cannot show.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import TYPE_CHECKING

import pytest

from evals.memory.sandbox import RedisSandbox
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot
from sdk.alfred_sdk.live_state import (
    LiveStateEntry,
    LiveStateWriter,
    live_state_key,
    read_live_state,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from shared.types import AioRedis

pytestmark = pytest.mark.skipif(
    os.getenv("ALFRED_MEMORY_EVAL_DOCKER") != "1" or shutil.which("docker") is None,
    reason="set ALFRED_MEMORY_EVAL_DOCKER=1 to run against a throwaway Redis container",
)

KEY = live_state_key("home-service")
# Big enough that one replace reaches the server over several socket reads, so a reader
# can land between its DEL and its HSET when the two are not one MULTI/EXEC. At 200
# entities a non-transactional replace still passed every run.
HOUSE_SIZE = 2000


def _url(redis: AioRedis) -> str:
    kwargs = redis.connection_pool.connection_kwargs
    return f"redis://{kwargs['host']}:{kwargs['port']}/{kwargs.get('db', 0)}"


def _house(prefix: str, state: str, n: int = HOUSE_SIZE) -> ContextSnapshot:
    return ContextSnapshot(
        controllable={
            "light": [ContextEntry(entity_id=f"light.{prefix}_{i}", state=state) for i in range(n)]
        }
    )


def _ids(prefix: str, n: int = HOUSE_SIZE) -> set[str]:
    return {f"light.{prefix}_{i}" for i in range(n)}


@pytest.fixture
async def redis() -> AsyncIterator[AioRedis]:
    async with RedisSandbox() as client:
        yield client


@pytest.fixture
async def writer(redis: AioRedis) -> AsyncIterator[LiveStateWriter]:
    live = LiveStateWriter(_url(redis), "home-service")
    yield live
    await live.aclose()


async def test_a_reader_never_sees_half_a_replace(redis: AioRedis, writer: LiveStateWriter) -> None:
    old, new = _house("old", "on"), _house("new", "off")
    await writer.replace(old)
    seen: list[set[str]] = []
    done = asyncio.Event()

    async def watch() -> None:
        while not done.is_set():
            fields = await redis.hkeys(KEY)
            seen.append({f.decode() if isinstance(f, bytes) else f for f in fields})

    watcher = asyncio.create_task(watch())
    for house in (new, old, new, old, new):
        await writer.replace(house)
    done.set()
    await watcher

    assert seen, "the watcher never read"
    assert all(ids in (_ids("old"), _ids("new")) for ids in seen)


async def test_an_update_requested_before_a_replace_never_lands_after_it(
    redis: AioRedis, writer: LiveStateWriter
) -> None:
    update = asyncio.create_task(
        writer.update("light", "controllable", ContextEntry(entity_id="light.lamp", state="on"))
    )
    replace = asyncio.create_task(
        writer.replace(
            ContextSnapshot(
                controllable={"light": [ContextEntry(entity_id="light.lamp", state="off")]}
            )
        )
    )
    await asyncio.gather(update, replace)

    raw = await redis.hget(KEY, "light.lamp")
    assert raw is not None
    assert LiveStateEntry.model_validate_json(raw).state == "off"


async def test_clear_removes_the_hash(redis: AioRedis, writer: LiveStateWriter) -> None:
    await writer.replace(_house("x", "on", n=3))
    await writer.clear()
    assert await redis.exists(KEY) == 0


async def test_the_reader_sees_what_the_writer_wrote(
    redis: AioRedis, writer: LiveStateWriter
) -> None:
    await redis.hset("alfred:tool_registry", "home-service", "{}")
    await writer.replace(_house("x", "on", n=2))
    await writer.update("light", "controllable", ContextEntry(entity_id="light.x_1", state="off"))
    await writer.remove("light.x_0")

    snapshot = await read_live_state(redis)

    assert snapshot is not None
    assert snapshot.controllable == {"light": [ContextEntry(entity_id="light.x_1", state="off")]}
