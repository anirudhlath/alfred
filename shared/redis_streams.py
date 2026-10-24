"""Typed async wrappers for Redis stream reads.

redis-py's asyncio stream-read methods (``xread``, ``xreadgroup``,
``xrange``, ``xrevrange``) are declared via an ``@overload`` pair keyed off an
``_is_async_client`` Protocol marker that mypy cannot resolve against
``redis.asyncio.Redis`` — every call site previously had to repeat the same
verbose return-type annotation plus a
``# type: ignore[assignment,misc,unused-ignore]``. These wrappers own that
gap once so callers get a plain, correctly typed coroutine.

This module also owns the one construction point for async Redis clients
used by blocking stream readers — see ``create_redis`` below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import redis.asyncio as aioredis
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime, timedelta

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


async def append_capped(
    redis: AioRedis,
    stream: str,
    fields: Mapping[str, str],
    *,
    maxlen: int,
    max_age: timedelta,
    now: datetime,
) -> None:
    """Append one entry to *stream*, then trim it by length and by age. Raises on failure.

    ``XADD … MAXLEN ~ maxlen`` bounds the length cheaply, but approximate trimming drops only
    whole radix nodes, so on a small stream it would never age anything out. The age bound
    is therefore a separate exact ``XTRIM … MINID`` (entry ids lead with their unix-ms), sent
    in the same non-transactional pipeline: one round trip, no MULTI. The caller decides
    what a refused write means (these logs are best-effort bookkeeping).
    """
    min_id = f"{int((now - max_age).timestamp() * 1000)}-0"
    async with redis.pipeline(transaction=False) as pipe:
        pipe.xadd(stream, cast("Any", fields), maxlen=maxlen, approximate=True)
        pipe.xtrim(stream, minid=min_id, approximate=False)
        await pipe.execute()


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
    max_id: str = "+",
    min_id: str = "-",
) -> list[tuple[bytes | str, dict[bytes | str, bytes | str]]]:
    """Typed ``XREVRANGE`` — owns the stub-gap ignore for the whole codebase.

    ``max_id``/``min_id`` bound the scan (newest first); the defaults cover
    the whole stream.
    """
    entries: list[tuple[bytes | str, dict[bytes | str, bytes | str]]]
    entries = await redis.xrevrange(  # type: ignore[assignment,misc,unused-ignore]
        stream, max=max_id, min=min_id, count=count
    )
    return entries


async def forward_range(
    redis: AioRedis,
    stream: str,
    *,
    min_id: str = "-",
    max_id: str = "+",
    count: int | None = None,
) -> list[tuple[bytes | str, dict[bytes | str, bytes | str]]]:
    """Typed ``XRANGE`` — owns the stub-gap ignore for the whole codebase.

    ``min_id``/``max_id`` bound the scan (oldest first); the defaults cover the whole
    stream, and ``count=None`` returns every entry in the bounds.
    """
    entries: list[tuple[bytes | str, dict[bytes | str, bytes | str]]]
    entries = await redis.xrange(  # type: ignore[assignment,misc,unused-ignore]
        stream, min=min_id, max=max_id, count=count
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


# A reclaimed entry is only worth acting on while it still describes the present.
MAX_REPLAY_AGE_MS = 300_000  # 5 minutes
# How long a per-entry replay marker lives: twice the oldest entry the reclaim pass will replay,
# so a replay still finds the marker its first delivery left.
REPLAY_MARKER_TTL_SECONDS = 2 * MAX_REPLAY_AGE_MS // 1000


def is_replayable(
    entry_id: bytes | str, *, now_ms: int, max_age_ms: int = MAX_REPLAY_AGE_MS
) -> bool:
    """Is this reclaimed stream entry recent enough to still act on?

    Redis stream ids are ``<unix-ms>-<seq>``. Entries older than ``max_age_ms`` are
    ACKed and dropped rather than replayed — reacting to a stale state change, or
    answering a question asked hours ago, is worse than missing it. An id we cannot
    parse is treated as too old (fail closed).
    """
    created_ms = entry_ms(entry_id)
    if created_ms is None:
        return False
    return now_ms - created_ms <= max_age_ms


def entry_ms(entry_id: bytes | str) -> int | None:
    """When Redis added the entry: the ``<unix-ms>`` half of its id, or None if unparseable."""
    raw = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
    try:
        return int(raw.split("-", 1)[0])
    except ValueError:
        return None


async def reclaim_replayable(
    redis: AioRedis,
    stream: str,
    group: str,
    consumer: str,
    *,
    now_ms: int,
    max_age_ms: int = MAX_REPLAY_AGE_MS,
) -> list[tuple[bytes | str, dict[bytes | str, bytes | str]]]:
    """Reclaim un-ACKed entries, returning only those still worth acting on.

    Entries past ``max_age_ms`` are ACKed and discarded: they still have to leave the
    pending-entries list (that is the leak being fixed), but replaying an hours-old
    entry would drive the system from history rather than from now.
    """
    claimed = await reclaim_stale(redis, stream, group, consumer)
    replayable: list[tuple[bytes | str, dict[bytes | str, bytes | str]]] = []
    expired: list[bytes | str] = []
    for entry_id, entry_data in claimed:
        if is_replayable(entry_id, now_ms=now_ms, max_age_ms=max_age_ms):
            replayable.append((entry_id, entry_data))
        else:
            expired.append(entry_id)
    for entry_id in expired:
        await redis.xack(stream, group, entry_id)
    if expired:
        logger.warning(
            "Dropped {} stale pending entries on '{}' (older than {}ms)",
            len(expired),
            stream,
            max_age_ms,
        )
    if replayable:
        logger.info("Reclaimed {} pending entries on '{}'", len(replayable), stream)
    return replayable


@dataclass(frozen=True)
class Redelivery:
    """A pending entry claimed for another try."""

    entry_id: bytes | str
    fields: dict[bytes | str, bytes | str]
    delivery: int  # deliveries including this one


async def reclaim_due(
    redis: AioRedis,
    stream: str,
    group: str,
    consumer: str,
    *,
    base_idle_ms: int = 15_000,
    count: int = 10,
) -> list[Redelivery]:
    """Claim the pending entries whose backoff has elapsed, counting their deliveries.

    Redis keeps each pending entry's ``times_delivered``; an entry delivered *n* times is
    due once it has been idle ``base_idle_ms * 2 ** (n - 1)`` (15 s, then 30 s). Each due
    entry is ``XCLAIM``ed with that same threshold as ``min_idle_time``, so a concurrent
    claimer cannot take it twice, and the claim itself counts the new delivery.

    An entry trimmed from the stream leaves the pending list: Redis 7 drops it inside
    ``XCLAIM``; an older server hands back a nil entry, which is ACKed here. A Redis
    error is logged and answered with ``[]``, like ``reclaim_stale``.
    """
    try:
        # count=10 is enough for one user: the single sequential consumer holds at most the
        # requests that failed in the last few minutes, and a person rarely has more than a
        # few unanswered at once. Beyond 10, XPENDING reads from the head, so the newer
        # entries wait until older ones leave; by then they may be past the replay window
        # and get FAILED_REPLY rather than a retry, which still answers each one once.
        pending: Any = await redis.xpending_range(stream, group, min="-", max="+", count=count)
    except Exception as exc:
        logger.warning("XPENDING failed on '{}' ({}) — skipping redelivery", stream, exc)
        return []
    due: list[Redelivery] = []
    for item in pending:
        entry_id = item["message_id"]
        delivered = int(item["times_delivered"])
        threshold = base_idle_ms * 2 ** max(delivered - 1, 0)
        if int(item["time_since_delivered"]) < threshold:
            continue
        try:
            claimed: Any = await redis.xclaim(
                stream, group, consumer, min_idle_time=threshold, message_ids=[entry_id]
            )
            if not claimed:
                continue  # another consumer claimed it first, or Redis dropped it
            fields = claimed[0][1]
            if not fields:
                await redis.xack(stream, group, entry_id)
                continue
        except Exception as exc:
            # Whatever was claimed already is still returned, or it would wait out a
            # longer backoff for nothing.
            logger.warning("XCLAIM failed on '{}' ({}) — retrying next pass", stream, exc)
            break
        due.append(Redelivery(entry_id=entry_id, fields=fields, delivery=delivered + 1))
    if due:
        logger.info("Reclaimed {} pending entries on '{}' for redelivery", len(due), stream)
    return due
