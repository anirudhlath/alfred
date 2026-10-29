"""The eval container's bus: what the driver reads from Alfred and does through it.

Reads:
- ``alfred:events`` (TriggerCreated and TriggerFired);
- ``alfred:actions``, where a trigger with an action fires (an ActionRequest);
- either of those two, waiting on them with blocking reads;
- the notification dispatch stream;
- the deferred list;
- the registry's reflex-audience tools, which are what Reflex's prompt shows;
- the stored user timezone;
- the user responses stream: its tail, and the follow-up a resumed run publishes;
- Reflex's decision log, waiting on it with a blocking read.

Acts:
- pulls a time trigger's ``run_at`` to now;
- deletes triggers;
- sets the user timezone, which is Reflex's clock;
- sets do-not-disturb through the admin API, as the PWA does;
- sets Reflex's settings through the admin API, as the owner does, and puts back what it found;
- confirms or denies a pending action through the web channel's API, as the Door does;
- clears do-not-disturb;
- clears Reflex's record of what people just changed;
- clears Reflex's asks: the open one, the repeat and day-count keys and the answered markers.

Entries come back raw; ``collect.py`` parses them. The Redis client is read through a
callable, so a stack restart's new client needs nothing re-wired.

Every call is bounded: a container frozen with its socket still open answers nothing, and
the harness raises ``BusError`` rather than hang the sample (``ContainerBus.timeout_s``).
"""

from __future__ import annotations

import asyncio
import functools
import json
import math
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Concatenate, Literal, Protocol
from uuid import uuid4

import httpx
from pydantic import ValidationError
from redis.exceptions import RedisError

from bus.schemas.events import PENDING_RECORD, ActionRequest, AlfredResponse
from core.identity.auth_middleware import COOKIE_NAME
from core.reflex.decision_log import DecisionLog
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolRegistry
from shared import usertime
from shared.redis_streams import forward_range, read, revrange
from shared.streams import (
    ACTIONS_STREAM,
    AUTH_SESSION_PREFIX,
    DEFERRED_NOTIFICATIONS_KEY,
    DND_STATE_KEY,
    EVENTS_STREAM,
    NOTIFICATION_DISPATCH_STREAM,
    PENDING_PREFIX,
    REFLEX_ANSWERED_PREFIX,
    REFLEX_ASK_OPEN_KEY,
    REFLEX_ASKED_PREFIX,
    REFLEX_ASKS_PREFIX,
    REFLEX_DECISION_LOG_STREAM,
    REFLEX_PERSON_CHANGES_KEY,
    TRIGGER_SYNC_OP_DELETED,
    TRIGGER_SYNC_OP_SAVED,
    TRIGGER_SYNC_OP_TZ_CHANGED,
    TRIGGERS_CHANGED_CHANNEL,
    TRIGGERS_KEY,
    USER_RESPONSES_STREAM,
    USER_TIMEZONE_KEY,
    decode_stream_value,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    from core.reflex.decision_log import DecisionRecord
    from core.reflex.tool_registry import ToolInfo
    from shared.types import AioRedis

EVAL_CREDENTIAL = "alfred-eval"  # the credential_id on the harness's admin sessions
_SESSION_TTL_S = 300  # a backstop: the session is deleted right after its one call
_MAX_SEQ = 2**64 - 1  # the last sequence number a stream id's millisecond can hold
# What the approval endpoints answer with: done, malformed id, gone, a crash, the store
# down. Anything else (401, 403, 405, 422, a gateway's 502) means the harness's own session
# or request is wrong, which no golden should score.
DOOR_ANSWERS = frozenset(
    {
        HTTPStatus.OK,
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.SERVICE_UNAVAILABLE,
    }
)


class BusError(RuntimeError):
    """The bus could not do what the driver asked. The harness failed, not Alfred."""


@dataclass(frozen=True)
class Entry:
    wall: float  # seconds since the epoch, from the stream entry's id
    data: dict[str, str]


class Bus(Protocol):
    async def events(self, since_wall: float) -> list[Entry]: ...
    async def actions(self, since_wall: float) -> list[Entry]: ...
    async def pending_actions(self) -> list[ActionRequest]: ...
    async def delete_pending_actions(self, request_ids: list[str]) -> None: ...
    async def wait_for_event(
        self, since_wall: float, timeout_s: float, wanted: Callable[[Entry], bool]
    ) -> bool: ...
    async def notifications(self, since_wall: float) -> list[Entry]: ...
    async def decision_records(self, since: datetime) -> list[DecisionRecord]: ...
    async def wait_for_decisions(
        self,
        since: datetime,
        timeout_s: float,
        enough: Callable[[list[DecisionRecord]], bool],
    ) -> list[DecisionRecord]: ...
    async def clear_person_changes(self) -> None: ...
    async def clear_reflex_asks(self) -> None: ...
    async def deferred(self) -> list[str]: ...
    async def reflex_tools(self) -> list[ToolInfo]: ...
    async def advance_trigger(self, trigger_id: str, now: datetime) -> bool: ...
    async def delete_triggers(self, trigger_ids: list[str]) -> None: ...
    async def user_timezone(self) -> str | None: ...
    async def set_user_timezone(self, tz: str | None) -> None: ...
    async def set_dnd(self, active: bool) -> None: ...
    async def clear_dnd(self) -> None: ...
    async def set_reflex_settings(self, change: dict[str, Any]) -> dict[str, Any]: ...
    async def restore_reflex_settings(self, document: dict[str, Any]) -> None: ...
    async def decide_pending(
        self, request_id: str, decision: Literal["confirm", "deny"]
    ) -> int: ...
    async def responses_tail(self) -> str: ...
    async def followup(
        self, in_reply_to: str, *, since_id: str, timeout_s: float
    ) -> AlfredResponse | None: ...


def _entry(entry_id: bytes | str, fields: dict[bytes | str, bytes | str]) -> Entry:
    ms = int(decode_stream_value(entry_id).split("-", 1)[0])
    data = {decode_stream_value(k): decode_stream_value(v) for k, v in fields.items()}
    return Entry(wall=ms / 1000, data=data)


def _response(entry_id: str, fields: dict[bytes | str, bytes | str]) -> AlfredResponse | None:
    """The response in a user responses entry: None for an entry that carries none, a
    ``BusError`` for one that carries a response the harness cannot read."""
    raw = fields.get(b"event") or fields.get("event")
    if not raw:
        return None
    try:
        return AlfredResponse.model_validate_json(decode_stream_value(raw))
    except ValidationError as exc:
        raise BusError(f"unreadable response {entry_id} on {USER_RESPONSES_STREAM}: {exc}") from exc


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


def _bounded[**P, R](
    method: Callable[Concatenate[ContainerBus, P], Coroutine[Any, Any, R]],
) -> Callable[Concatenate[ContainerBus, P], Coroutine[Any, Any, R]]:
    """Bound a bus call by the bus's ``timeout_s``: past it, a ``BusError``."""

    @functools.wraps(method)
    async def bounded(bus: ContainerBus, /, *args: P.args, **kwargs: P.kwargs) -> R:
        async with bus.answered(method.__name__):
            return await method(bus, *args, **kwargs)

    return bounded


class ContainerBus:
    """The bus on one eval container.

    *timeout_s* bounds every call that does not block by design (and the admin API's HTTP
    request): Redis answers those in milliseconds, so one that takes this long is a frozen
    container. ``wait_for_event`` gets its own wait plus the same margin.
    """

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

    @asynccontextmanager
    async def answered(self, what: str, *, wait_s: float = 0.0) -> AsyncIterator[None]:
        """Raise ``BusError`` if the block takes longer than *wait_s* plus ``timeout_s``."""
        limit_s = wait_s + self._timeout_s
        try:
            async with asyncio.timeout(limit_s):
                yield
        except TimeoutError as exc:
            raise BusError(
                f"the container's Redis did not answer {what} within {limit_s:g} s"
            ) from exc

    async def _range(self, stream: str, since_wall: float) -> list[Entry]:
        raw = await forward_range(self._redis(), stream, min_id=str(window_start_ms(since_wall)))
        return [_entry(entry_id, fields) for entry_id, fields in raw]

    @_bounded
    async def events(self, since_wall: float) -> list[Entry]:
        return await self._range(EVENTS_STREAM, since_wall)

    @_bounded
    async def actions(self, since_wall: float) -> list[Entry]:
        return await self._range(ACTIONS_STREAM, since_wall)

    @_bounded
    async def pending_actions(self) -> list[ActionRequest]:
        """Read typed pending entries without consuming them: a router-held action as
        itself, a parked run as one request per deferred call, all under the record's id.
        Expiry mid-scan is normal; malformed data or a failed read is missing evidence,
        never a silent omission."""
        actions = []
        r = self._redis()
        try:
            async for key in r.scan_iter(match=f"{PENDING_PREFIX}*", count=100):
                raw = await r.get(key)
                if raw is None:
                    continue
                record = PENDING_RECORD.validate_json(decode_stream_value(raw), strict=True)
                if decode_stream_value(key) != f"{PENDING_PREFIX}{record.record_id}":
                    raise ValueError("pending key does not match its record id")
                actions.extend(record.action_requests())
        except (ValueError, RedisError) as exc:
            raise BusError(f"could not read pending actions: {exc}") from exc
        return actions

    @_bounded
    async def delete_pending_actions(self, request_ids: list[str]) -> None:
        """Remove only the sample's owned pending IDs, never the whole pending store."""
        if request_ids:
            await self._redis().delete(
                *(f"{PENDING_PREFIX}{i}" for i in dict.fromkeys(request_ids))
            )

    async def wait_for_event(
        self, since_wall: float, timeout_s: float, wanted: Callable[[Entry], bool]
    ) -> bool:
        """Wait for an ``alfred:events`` or ``alfred:actions`` entry at or after
        *since_wall* that *wanted* accepts. False after *timeout_s*.

        Each read blocks until an entry lands on either stream after the last one seen
        there, so the wait wakes as the entry lands, and an entry already in the window is
        seen at once. A container that answers no read within *timeout_s* plus the bus's
        ``timeout_s`` is a ``BusError``.
        """
        async with self.answered("wait_for_event", wait_s=timeout_s):
            return await self._wait_for_event(since_wall, timeout_s, wanted)

    async def _wait_for_event(
        self, since_wall: float, timeout_s: float, wanted: Callable[[Entry], bool]
    ) -> bool:
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

    @_bounded
    async def responses_tail(self) -> str:
        """The id of the user responses stream's last entry, or ``0-0`` for an empty one: the
        point ``followup`` reads on from, as ``publish_and_wait`` anchors its own reads."""
        tail = await revrange(self._redis(), USER_RESPONSES_STREAM, count=1)
        return decode_stream_value(tail[0][0]) if tail else "0-0"

    async def followup(
        self, in_reply_to: str, *, since_id: str, timeout_s: float
    ) -> AlfredResponse | None:
        """Wait for the follow-up to the request *in_reply_to*: a response with ``followup``
        set that names it, after the entry *since_id*. Anything else on the stream (a
        follow-up to another request, a reply someone was waiting for) is passed over.
        None after *timeout_s*.

        Each read blocks until an entry lands, so the wait wakes as the response is
        published. A container that answers no read within *timeout_s* plus the bus's
        ``timeout_s`` is a ``BusError``, as is an entry that is not a readable response.
        """
        async with self.answered("followup", wait_s=timeout_s):
            return await self._followup(in_reply_to, since_id, timeout_s)

    async def _followup(
        self, in_reply_to: str, since_id: str, timeout_s: float
    ) -> AlfredResponse | None:
        r = self._redis()
        last_id = since_id
        deadline = time.monotonic() + timeout_s
        while True:
            remaining_ms = math.ceil((deadline - time.monotonic()) * 1000)
            # BLOCK 0 blocks for ever, so past the deadline the last read does not block.
            block = remaining_ms if remaining_ms > 0 else None
            for _stream, entries in await read(r, {USER_RESPONSES_STREAM: last_id}, block=block):
                for entry_id, fields in entries:
                    last_id = decode_stream_value(entry_id)
                    response = _response(last_id, fields)
                    if response and response.followup and response.in_reply_to == in_reply_to:
                        return response
            if block is None:
                return None

    @_bounded
    async def notifications(self, since_wall: float) -> list[Entry]:
        return await self._range(NOTIFICATION_DISPATCH_STREAM, since_wall)

    @_bounded
    async def decision_records(self, since: datetime) -> list[DecisionRecord]:
        """Reflex's decisions logged at or after *since* (timezone-aware), oldest first."""
        return await DecisionLog(self._redis()).read(since=since)

    async def wait_for_decisions(
        self,
        since: datetime,
        timeout_s: float,
        enough: Callable[[list[DecisionRecord]], bool],
    ) -> list[DecisionRecord]:
        """Reflex's decisions logged at or after *since*, once *enough* accepts them, or what
        the log holds after *timeout_s*.

        Reflex logs a decision just after its model's reply returns, so a read made the
        moment the proxy records the call can come first. Each wait blocks until an entry
        lands on the log after the last one seen, so it wakes as the record is written, and
        a record already there is seen at once. A container that answers no read within
        *timeout_s* plus the bus's ``timeout_s`` is a ``BusError``.
        """
        async with self.answered("wait_for_decisions", wait_s=timeout_s):
            return await self._wait_for_decisions(since, timeout_s, enough)

    async def _wait_for_decisions(
        self,
        since: datetime,
        timeout_s: float,
        enough: Callable[[list[DecisionRecord]], bool],
    ) -> list[DecisionRecord]:
        r = self._redis()
        log = DecisionLog(r)
        # The tail first, then the read: a record written between the two is in the read, and
        # one written after the read is after the tail, so the blocking read below sees it.
        tail = await revrange(r, REFLEX_DECISION_LOG_STREAM, count=1)
        last_id = decode_stream_value(tail[0][0]) if tail else "0-0"
        deadline = time.monotonic() + timeout_s
        while True:
            records = await log.read(since=since)
            remaining_ms = math.ceil((deadline - time.monotonic()) * 1000)
            if enough(records) or remaining_ms <= 0:
                return records
            for _stream, entries in await read(
                r, {REFLEX_DECISION_LOG_STREAM: last_id}, block=remaining_ms
            ):
                last_id = decode_stream_value(entries[-1][0])

    @_bounded
    async def clear_person_changes(self) -> None:
        """Drop Reflex's record of what people just changed. The restore pushes every drifted
        entity back as ``outside``, which Reflex notes as a person's change: left there, the
        next sample's prompt would tell the model to leave those devices alone."""
        await self._redis().delete(REFLEX_PERSON_CHANGES_KEY)

    @_bounded
    async def clear_reflex_asks(self) -> None:
        """Drop Reflex's asking state: the open ask, the repeat and day-count keys, and the
        answered markers. A left-over open ask would hold the next sample's ask, and a repeat
        key would hold it for the hours ``repeat_hours`` says. The answers stream stays: it is
        the record, and no sample reads it. SCAN, never KEYS, which blocks Redis."""
        r = self._redis()
        await r.delete(REFLEX_ASK_OPEN_KEY)
        for prefix in (REFLEX_ASKED_PREFIX, REFLEX_ASKS_PREFIX, REFLEX_ANSWERED_PREFIX):
            keys = [key async for key in r.scan_iter(match=f"{prefix}*", count=100)]
            if keys:
                await r.delete(*keys)

    @_bounded
    async def deferred(self) -> list[str]:
        raw: list[Any] = await self._redis().lrange(DEFERRED_NOTIFICATIONS_KEY, 0, -1)
        return [decode_stream_value(r) for r in raw]

    @_bounded
    async def reflex_tools(self) -> list[ToolInfo]:
        tools = await ToolRegistry(self._redis()).get_tools()
        return [t for t in tools if t.audience == REFLEX_AUDIENCE]

    async def _publish(self, payload: dict[str, str]) -> None:
        await self._redis().publish(TRIGGERS_CHANGED_CHANNEL, json.dumps(payload))

    @_bounded
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

    @_bounded
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
        sample restarts it on a fresh one too. Inside the window, the triggers process
        registering is a harness failure (``collect.trigger_records``), and a resurrected
        trigger's notification is not counted (``checks.notifications.sample_trigger``)."""
        r = self._redis()
        for trigger_id in trigger_ids:
            if await r.hdel(TRIGGERS_KEY, trigger_id):
                await self._publish({"op": TRIGGER_SYNC_OP_DELETED, "trigger_id": trigger_id})

    @_bounded
    async def user_timezone(self) -> str | None:
        """The stored zone. None when nothing is stored: Alfred then falls back to its env."""
        raw = await self._redis().get(USER_TIMEZONE_KEY)
        return None if raw is None else decode_stream_value(raw)

    @_bounded
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

    async def _signed_in(
        self, what: str, method: Literal["GET", "POST", "PUT"], path: str, body: Any = None
    ) -> httpx.Response:
        """Call *path* on the web channel as the signed-in owner and return the response,
        whatever its status. The session is minted for this one call and deleted after it.
        Each Redis step is bounded by ``timeout_s``, and so is the request. *what* names
        the bus method, for the bound's message."""
        r = self._redis()
        session_id = uuid4().hex
        key = f"{AUTH_SESSION_PREFIX}{session_id}"
        try:
            # One transaction, so the session never exists without its TTL; inside the
            # try, so a write whose reply is lost is still deleted.
            async with (
                self.answered(f"{what}'s session write"),
                r.pipeline(transaction=True) as pipe,
            ):
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
                return await client.request(
                    method,
                    f"{self._web_url()}{path}",
                    json=body,
                    headers={"Cookie": f"{COOKIE_NAME}={session_id}"},
                    timeout=self._timeout_s,
                )
        except httpx.HTTPError as exc:
            raise BusError(f"POST {path} failed: {exc}") from exc
        finally:
            # Bounded too, or a frozen container hangs the cleanup of the error above.
            async with self.answered(f"{what}'s session delete"):
                await r.delete(key)

    async def set_dnd(self, active: bool) -> None:
        """Turn do-not-disturb on or off through the admin API, as the PWA does. Turning it
        off queues the drain of held notifications, which deleting the key would not."""
        path = "/api/admin/dnd"
        response = await self._signed_in("set_dnd", "POST", path, {"active": active})
        if not response.is_success:
            raise BusError(f"POST {path} answered {response.status_code}: {response.text[:200]}")

    async def _reflex_settings_call(
        self, what: str, method: Literal["GET", "PUT"], body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """The settings document the admin API answers with, or a ``BusError``: a refused call
        (a 422 for a bad document included) is the harness's mistake, not Alfred's."""
        path = "/api/admin/reflex/settings"
        response = await self._signed_in(what, method, path, body)
        if not response.is_success:
            raise BusError(
                f"{method} {path} answered {response.status_code}: {response.text[:200]}"
            )
        try:
            document = response.json()
        except ValueError as exc:
            raise BusError(f"{method} {path} answered something that is not JSON") from exc
        if not isinstance(document, dict):
            raise BusError(f"{method} {path} answered {type(document).__name__}, not a document")
        return document

    async def set_reflex_settings(self, change: dict[str, Any]) -> dict[str, Any]:
        """Lay *change* over Reflex's settings through the admin API, as the owner does (the
        route takes a whole document, so this reads it first), and return the document it
        found, for ``restore_reflex_settings``. A field *change* does not name keeps the value
        the document had."""
        found = await self._reflex_settings_call("set_reflex_settings", "GET")
        await self._reflex_settings_call("set_reflex_settings", "PUT", {**found, **change})
        return found

    async def restore_reflex_settings(self, document: dict[str, Any]) -> None:
        """Put back the document ``set_reflex_settings`` found, whole."""
        await self._reflex_settings_call("restore_reflex_settings", "PUT", document)

    async def decide_pending(self, request_id: str, decision: Literal["confirm", "deny"]) -> int:
        """Confirm or deny a pending action through the web channel, as the Door does, and
        return the HTTP status when it is one of the product's answers (``DOOR_ANSWERS``):
        for the driver to record, since what a 404 or a 500 means is for the checks to score.
        Any other status (a refused session, a changed contract) is the harness's fault, not
        the product's, and raises, as does a request that gets no answer."""
        path = f"/api/actions/{request_id}/{decision}"
        response = await self._signed_in("decide_pending", "POST", path)
        if response.status_code not in DOOR_ANSWERS:
            raise BusError(f"POST {path} answered {response.status_code}: {response.text[:200]}")
        return response.status_code

    @_bounded
    async def clear_dnd(self) -> None:
        """Drop do-not-disturb and what it held, without a drain: a drain now would deliver
        this sample's held notifications in the next sample's window."""
        await self._redis().delete(DND_STATE_KEY, DEFERRED_NOTIFICATIONS_KEY)
