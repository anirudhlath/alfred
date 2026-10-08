"""The eval container's bus: what the driver reads from Alfred and does through it.

Reads:
- ``alfred:events`` (TriggerCreated and TriggerFired);
- ``alfred:actions``, where a trigger with an action fires (an ActionRequest);
- either of those two, waiting on them with blocking reads;
- the notification dispatch stream;
- the deferred list;
- the registry's reflex-audience tools, which are what Reflex's prompt shows;
- the stored user timezone.

Acts:
- pulls a time trigger's ``run_at`` to now;
- deletes triggers;
- sets the user timezone, which is Reflex's clock;
- sets do-not-disturb through the admin API, as the PWA does;
- clears do-not-disturb.

Entries come back raw; ``collect.py`` parses them. The Redis client is read through a
callable, so a stack restart's new client needs nothing re-wired.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

import httpx

from core.identity.auth_middleware import COOKIE_NAME
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolRegistry
from shared import usertime
from shared.redis_streams import forward_range, read
from shared.streams import (
    ACTIONS_STREAM,
    AUTH_SESSION_PREFIX,
    DEFERRED_NOTIFICATIONS_KEY,
    DND_STATE_KEY,
    EVENTS_STREAM,
    NOTIFICATION_DISPATCH_STREAM,
    TRIGGER_SYNC_OP_DELETED,
    TRIGGER_SYNC_OP_SAVED,
    TRIGGER_SYNC_OP_TZ_CHANGED,
    TRIGGERS_CHANGED_CHANNEL,
    TRIGGERS_KEY,
    USER_TIMEZONE_KEY,
    decode_stream_value,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.reflex.tool_registry import ToolInfo
    from shared.types import AioRedis

EVAL_CREDENTIAL = "alfred-eval"  # the credential_id on the harness's admin sessions
_SESSION_TTL_S = 300  # a backstop: the session is deleted right after its one call
_MAX_SEQ = 2**64 - 1  # the last sequence number a stream id's millisecond can hold


class BusError(RuntimeError):
    """The bus could not do what the driver asked. The harness failed, not Alfred."""


@dataclass(frozen=True)
class Entry:
    wall: float  # seconds since the epoch, from the stream entry's id
    data: dict[str, str]


class Bus(Protocol):
    async def events(self, since_wall: float) -> list[Entry]: ...
    async def actions(self, since_wall: float) -> list[Entry]: ...
    async def wait_for_event(
        self, since_wall: float, timeout_s: float, wanted: Callable[[Entry], bool]
    ) -> bool: ...
    async def notifications(self, since_wall: float) -> list[Entry]: ...
    async def deferred(self) -> list[str]: ...
    async def reflex_tools(self) -> list[ToolInfo]: ...
    async def advance_trigger(self, trigger_id: str, now: datetime) -> bool: ...
    async def delete_triggers(self, trigger_ids: list[str]) -> None: ...
    async def user_timezone(self) -> str | None: ...
    async def set_user_timezone(self, tz: str | None) -> None: ...
    async def set_dnd(self, active: bool) -> None: ...
    async def clear_dnd(self) -> None: ...


def _entry(entry_id: bytes | str, fields: dict[bytes | str, bytes | str]) -> Entry:
    ms = int(decode_stream_value(entry_id).split("-", 1)[0])
    data = {decode_stream_value(k): decode_stream_value(v) for k, v in fields.items()}
    return Entry(wall=ms / 1000, data=data)


def window_start_ms(since_wall: float) -> int:
    """The first stream-id millisecond of a window that starts at *since_wall*.

    Ids are whole milliseconds, so the start's own millisecond is in: an entry there may
    have come just after the start. The bus reads from it and the collectors keep from it.
    """
    return int(since_wall * 1000)


def zone_for_hour(hour: int, now: datetime) -> str:
    """The ``Etc/GMT`` zone whose local hour is *hour* at *now*.

    The offset is taken in -9..+14 hours, so every hour has exactly one zone, and every
    such zone exists (they run from UTC-12 to UTC+14). ``Etc/GMT`` signs are inverted:
    ``Etc/GMT-8`` is UTC+8.
    """
    offset = (hour - now.astimezone(UTC).hour) % 24
    if offset > 14:
        offset -= 24
    return "Etc/GMT" if offset == 0 else f"Etc/GMT{-offset:+d}"


class ContainerBus:
    def __init__(
        self,
        redis: Callable[[], AioRedis],
        web_url: Callable[[], str],
        *,
        http: Callable[[], httpx.AsyncClient] = httpx.AsyncClient,
        timeout_s: float = 10.0,
    ) -> None:
        self._redis = redis
        self._web_url = web_url
        self._http = http
        self._timeout_s = timeout_s

    async def _range(self, stream: str, since_wall: float) -> list[Entry]:
        raw = await forward_range(self._redis(), stream, min_id=str(window_start_ms(since_wall)))
        return [_entry(entry_id, fields) for entry_id, fields in raw]

    async def events(self, since_wall: float) -> list[Entry]:
        return await self._range(EVENTS_STREAM, since_wall)

    async def actions(self, since_wall: float) -> list[Entry]:
        return await self._range(ACTIONS_STREAM, since_wall)

    async def wait_for_event(
        self, since_wall: float, timeout_s: float, wanted: Callable[[Entry], bool]
    ) -> bool:
        """Wait for an ``alfred:events`` or ``alfred:actions`` entry at or after
        *since_wall* that *wanted* accepts. False after *timeout_s*.

        Each read blocks until an entry lands on either stream after the last one seen
        there, so the wait wakes as the entry lands, and an entry already in the window is
        seen at once.
        """
        r = self._redis()
        # XREAD returns the entries after the id it is given: start just before the window.
        start = f"{window_start_ms(since_wall) - 1}-{_MAX_SEQ}"
        last_ids = {EVENTS_STREAM: start, ACTIONS_STREAM: start}
        deadline = time.monotonic() + timeout_s
        while True:
            remaining_ms = math.ceil((deadline - time.monotonic()) * 1000)
            # BLOCK 0 blocks for ever, so past the deadline the last read does not block.
            block = remaining_ms if remaining_ms > 0 else None
            for stream, entries in await read(r, dict(last_ids), block=block):
                for entry_id, fields in entries:
                    last_ids[decode_stream_value(stream)] = decode_stream_value(entry_id)
                    if wanted(_entry(entry_id, fields)):
                        return True
            if block is None:
                return False

    async def notifications(self, since_wall: float) -> list[Entry]:
        return await self._range(NOTIFICATION_DISPATCH_STREAM, since_wall)

    async def deferred(self) -> list[str]:
        raw: list[Any] = await self._redis().lrange(DEFERRED_NOTIFICATIONS_KEY, 0, -1)
        return [decode_stream_value(r) for r in raw]

    async def reflex_tools(self) -> list[ToolInfo]:
        tools = await ToolRegistry(self._redis()).get_tools()
        return [t for t in tools if t.audience == REFLEX_AUDIENCE]

    async def _publish(self, payload: dict[str, str]) -> None:
        await self._redis().publish(TRIGGERS_CHANGED_CHANNEL, json.dumps(payload))

    async def advance_trigger(self, trigger_id: str, now: datetime) -> bool:
        """Make a stored time trigger due at *now*, and tell the engine the way
        ``TriggerStore.save`` does. Its scheduler wakes on the message and fires the
        trigger. False when the row is gone (a one-shot that already fired) or has no
        ``run_at`` (a cron trigger)."""
        r = self._redis()
        raw = await r.hget(TRIGGERS_KEY, trigger_id)
        if raw is None:
            return False
        row: dict[str, Any] = json.loads(decode_stream_value(raw))
        conditions = row.get("conditions")
        if not isinstance(conditions, dict) or conditions.get("run_at") is None:
            return False
        conditions["run_at"] = now.isoformat()
        await r.hset(TRIGGERS_KEY, trigger_id, json.dumps(row))
        await self._publish({"op": TRIGGER_SYNC_OP_SAVED, "trigger_id": trigger_id})
        return True

    async def delete_triggers(self, trigger_ids: list[str]) -> None:
        """Remove triggers as ``TriggerStore.delete`` does, except that each one's YAML
        snapshot stays in the data dir. Only a ``TriggerStore`` deletes a snapshot, and the
        harness can reach none: the triggers and conscious processes each hold one on the
        same snapshot dir, but the admin API has no delete route (it fires and enables,
        nothing more), and Conscious's ``delete_trigger`` tool runs only when the model
        calls it.

        That leaves one window. ``TriggerStore.load`` rehydrates from the YAML snapshots,
        HSETting them back, when ``alfred:triggers`` is empty, and both processes call it
        at startup. So if either restarts inside the container (the runner revives a
        crashed service) after this call has emptied the hash, the deleted triggers come
        back. The window closes with the container: each suite boots its own, on a fresh
        data dir, and a sample the harness fails leaves the stack dirty, so the next
        sample restarts it on a fresh one too."""
        r = self._redis()
        for trigger_id in trigger_ids:
            if await r.hdel(TRIGGERS_KEY, trigger_id):
                await self._publish({"op": TRIGGER_SYNC_OP_DELETED, "trigger_id": trigger_id})

    async def user_timezone(self) -> str | None:
        """The stored zone. None when nothing is stored: Alfred then falls back to its env."""
        raw = await self._redis().get(USER_TIMEZONE_KEY)
        return None if raw is None else decode_stream_value(raw)

    async def set_user_timezone(self, tz: str | None) -> None:
        r = self._redis()
        if tz is None:
            if await r.delete(USER_TIMEZONE_KEY):
                await self._publish({"op": TRIGGER_SYNC_OP_TZ_CHANGED})
            return
        # usertime.set_user_timezone returns False for an unchanged zone too, so validate
        # here rather than read its result.
        if not usertime.is_valid_timezone(tz):
            raise BusError(f"{tz!r} is not a time zone the harness's Python knows")
        await usertime.set_user_timezone(r, tz)

    async def set_dnd(self, active: bool) -> None:
        """Turn do-not-disturb on or off through the admin API, as the PWA does. Turning it
        off queues the drain of held notifications, which deleting the key would not. The
        admin session is minted for this one call and deleted after it."""
        r = self._redis()
        session_id = uuid4().hex
        key = f"{AUTH_SESSION_PREFIX}{session_id}"
        try:
            # One transaction, so the session never exists without its TTL; inside the
            # try, so a write whose reply is lost is still deleted.
            async with r.pipeline(transaction=True) as pipe:
                pipe.hset(
                    key,
                    mapping={
                        "authenticated": "1",
                        "credential_id": EVAL_CREDENTIAL,
                        "created_at": datetime.now(UTC).isoformat(),
                        "channel": "eval",
                    },
                )
                pipe.expire(key, _SESSION_TTL_S)
                await pipe.execute()
            async with self._http() as client:
                response = await client.post(
                    f"{self._web_url()}/api/admin/dnd",
                    json={"active": active},
                    headers={"Cookie": f"{COOKIE_NAME}={session_id}"},
                    timeout=self._timeout_s,
                )
        except httpx.HTTPError as exc:
            raise BusError(f"POST /api/admin/dnd failed: {exc}") from exc
        finally:
            await r.delete(key)
        if not response.is_success:
            raise BusError(
                f"POST /api/admin/dnd answered {response.status_code}: {response.text[:200]}"
            )

    async def clear_dnd(self) -> None:
        """Drop do-not-disturb and what it held, without a drain: a drain now would deliver
        this sample's held notifications in the next sample's window."""
        await self._redis().delete(DND_STATE_KEY, DEFERRED_NOTIFICATIONS_KEY)
