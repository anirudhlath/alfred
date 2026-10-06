"""Live state — Alfred's contract for what a service's devices are doing right now.

A service publishes its live state through ``LiveStateWriter`` and Alfred reads it with
``read_live_state()``. Both sides take the key and the entry format from this module, so
neither names the other.

Storage: one Redis hash per service at ``alfred:live_state:{service_name}``. Each field is
an entity ID and each value the JSON of a ``LiveStateEntry``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

import redis.asyncio as aioredis
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from redis.typing import EncodableT, FieldT

    from .context import ContextEntry, ContextSnapshot

logger = logging.getLogger(__name__)

LIVE_STATE_KEY_PREFIX = "alfred:live_state:"
# Writers are called inline from a service's event loop, so a Redis outage must never hang
# one. The bound is per Redis command, not per call: calls queue FIFO behind the write lock,
# so the N-th queued call can wait about N times this timeout.
WRITE_TIMEOUT_SECONDS = 5.0

LiveStateKind = Literal["controllable", "sensor"]


class LiveStateEntry(BaseModel):
    """One entity's live state, as stored in its service's hash."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    kind: LiveStateKind
    state: str
    attributes: dict[str, Any] = {}


def live_state_key(service_name: str) -> str:
    """The hash a service's live state lives in."""
    return f"{LIVE_STATE_KEY_PREFIX}{service_name}"


def _entry_json(domain: str, kind: LiveStateKind, entry: ContextEntry) -> str:
    return LiveStateEntry(
        domain=domain, kind=kind, state=entry.state, attributes=entry.attributes
    ).model_dump_json()


class LiveStateWriter:
    """The only way a service writes its live state.

    Holds one Redis client for the process's lifetime. Every write goes through one FIFO
    lock, so writes reach Redis in the order they were requested. A service applies an
    event to its own state before requesting the write and builds a ``replace`` snapshot
    from that same state with no await in between, so a snapshot never overwrites a newer
    update. ``aclose`` takes the same lock, and every write after it is a no-op.
    """

    def __init__(self, redis_url: str, service_name: str) -> None:
        self._key = live_state_key(service_name)
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url,
            socket_timeout=WRITE_TIMEOUT_SECONDS,
            socket_connect_timeout=WRITE_TIMEOUT_SECONDS,
        )
        self._lock = asyncio.Lock()
        self._closed = False

    async def replace(self, snapshot: ContextSnapshot) -> None:
        """Swap the whole hash for ``snapshot`` atomically (on connecting to the source)."""
        fields: dict[FieldT, EncodableT] = {}
        buckets: tuple[tuple[LiveStateKind, dict[str, list[ContextEntry]]], ...] = (
            ("controllable", snapshot.controllable),
            ("sensor", snapshot.sensors),
        )
        for kind, groups in buckets:
            for domain, entries in groups.items():
                for entry in entries:
                    fields[entry.entity_id] = _entry_json(domain, kind, entry)
        async with self._lock:
            if self._closed:
                return
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.delete(self._key)
                if fields:
                    pipe.hset(self._key, mapping=fields)
                await pipe.execute()

    async def update(self, domain: str, kind: LiveStateKind, entry: ContextEntry) -> None:
        """Write one entity's state (on each state change)."""
        value = _entry_json(domain, kind, entry)
        async with self._lock:
            if self._closed:
                return
            await self._redis.hset(self._key, entry.entity_id, value)

    async def remove(self, entity_id: str) -> None:
        """Drop one entity (the source deleted it)."""
        async with self._lock:
            if self._closed:
                return
            await self._redis.hdel(self._key, entity_id)

    async def clear(self) -> None:
        """Drop the whole hash (on disconnect, at startup, at shutdown)."""
        async with self._lock:
            if self._closed:
                return
            await self._redis.delete(self._key)

    async def aclose(self) -> None:
        """Close the client once the writes queued ahead have landed; later ones are dropped.

        Taking the write lock matters: redis-py's ``aclose()`` cuts in-use connections and
        leaves the pool usable, so without it a write in flight would be cut off and a
        queued one would reconnect and land after the close.
        """
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            await self._redis.aclose()
