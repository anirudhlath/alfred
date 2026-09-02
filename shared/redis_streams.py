"""Typed async wrappers for Redis stream reads.

redis-py's asyncio stream-read methods (``xread``, ``xreadgroup``,
``xrevrange``) are declared via an ``@overload`` pair keyed off an
``_is_async_client`` Protocol marker that mypy cannot resolve against
``redis.asyncio.Redis`` — every call site previously had to repeat the same
verbose return-type annotation plus a
``# type: ignore[assignment,misc,unused-ignore]``. These wrappers own that
gap once so callers get a plain, correctly typed coroutine.

This module also owns the one construction point for async Redis clients
used by blocking stream readers — see ``create_redis`` below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import redis.asyncio as aioredis
from loguru import logger

if TYPE_CHECKING:
    from shared.types import AioRedis


def create_redis(url: str, *, decode_responses: bool = False) -> AioRedis:
    """Create an async Redis client with socket_timeout=None (redis-py 8 defaults to 5s,
    which breaks idle blocking stream reads — block= governs read timeouts instead).
    """
    return aioredis.from_url(url, decode_responses=decode_responses, socket_timeout=None)


# xread/xreadgroup return shape: one entry per stream, each stream carrying a
# list of (entry_id, fields) pairs.
type StreamBatch = list[
    tuple[bytes | str, list[tuple[bytes | str, dict[bytes | str, bytes | str]]]]
]


async def read_group(
    redis: AioRedis,
    group: str,
    consumer: str,
    streams: dict[str, str],
    *,
    count: int | None = None,
    block: int | None = None,
) -> StreamBatch:
    """Typed ``XREADGROUP`` — owns the stub-gap ignore for the whole codebase."""
    entries: StreamBatch = await redis.xreadgroup(  # type: ignore[assignment,misc,unused-ignore]
        group, consumer, cast("Any", streams), count=count, block=block
    )
    return entries


async def read(
    redis: AioRedis,
    streams: dict[str, str],
    *,
    count: int | None = None,
    block: int | None = None,
) -> StreamBatch:
    """Typed ``XREAD`` — owns the stub-gap ignore for the whole codebase."""
    entries: StreamBatch = await redis.xread(  # type: ignore[assignment,misc,unused-ignore]
        cast("Any", streams), count=count, block=block
    )
    return entries


async def revrange(
    redis: AioRedis,
    stream: str,
    *,
    count: int,
) -> list[tuple[bytes | str, dict[bytes | str, bytes | str]]]:
    """Typed ``XREVRANGE`` — owns the stub-gap ignore for the whole codebase."""
    entries: list[tuple[bytes | str, dict[bytes | str, bytes | str]]]
    entries = await redis.xrevrange(  # type: ignore[assignment,misc,unused-ignore]
        stream, count=count
    )
    return entries


async def reclaim_stale(
    redis: AioRedis,
    stream: str,
    group: str,
    consumer: str,
    *,
    min_idle_ms: int = 60_000,
    count: int = 10,
) -> list[tuple[bytes | str, dict[bytes | str, bytes | str]]]:
    """Typed ``XAUTOCLAIM`` — reclaim messages a consumer read but never ACKed.

    ``XREADGROUP`` with ``>`` only ever delivers messages the group has never seen,
    so an un-ACKed entry is NOT redelivered — it sits in the pending-entries list
    until something explicitly claims it. Every consumer loop that ACKs on success
    only needs to call this periodically or it silently drops failed work and grows
    an unbounded PEL.

    Returns the claimed entries, or ``[]`` when nothing is stale (or the server is
    too old for XAUTOCLAIM — degrade rather than kill the caller's loop).
    """
    try:
        claimed: Any = await redis.xautoclaim(  # type: ignore[misc,unused-ignore]
            stream, group, consumer, min_idle_time=min_idle_ms, start_id="0-0", count=count
        )
    except Exception as exc:
        logger.warning("XAUTOCLAIM unavailable on '{}' ({}) — skipping PEL recovery", stream, exc)
        return []
    entries: list[tuple[bytes | str, dict[bytes | str, bytes | str]]] = (
        claimed[1] if claimed and len(claimed) > 1 and claimed[1] else []
    )
    return entries
