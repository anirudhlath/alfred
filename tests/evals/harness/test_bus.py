from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest
from redis import exceptions as redis_exceptions

from core.reflex.tool_registry import ToolRegistry
from evals.harness.bus import BusError, ContainerBus, zone_for_hour
from shared.redis_streams import read
from shared.streams import (
    AUTH_SESSION_PREFIX,
    DEFERRED_NOTIFICATIONS_KEY,
    DND_STATE_KEY,
    EVENTS_STREAM,
    NOTIFICATION_DISPATCH_STREAM,
    TRIGGERS_CHANGED_CHANNEL,
    TRIGGERS_KEY,
    USER_TIMEZONE_KEY,
)

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


def at(hour: int, minute: int = 30) -> datetime:
    return datetime(2026, 10, 8, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("hour", "utc_hour", "zone"),
    [
        (22, 15, "Etc/GMT-7"),
        (2, 20, "Etc/GMT-6"),
        (3, 12, "Etc/GMT+9"),
        (15, 15, "Etc/GMT"),
        (10, 20, "Etc/GMT-14"),
    ],
)
def test_zone_for_hour(hour: int, utc_hour: int, zone: str) -> None:
    assert zone_for_hour(hour, at(utc_hour)) == zone


def test_every_hour_has_a_zone_that_shows_it_at_any_utc_hour() -> None:
    for utc_hour in range(24):
        now = at(utc_hour, 57)
        for hour in range(24):
            assert now.astimezone(ZoneInfo(zone_for_hour(hour, now))).hour == hour


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


async def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={})


def bus(redis: fakeredis.FakeAsyncRedis, handler: Handler = ok) -> ContainerBus:
    transport = httpx.MockTransport(handler)
    return ContainerBus(
        lambda: redis,
        lambda: "http://alfred.test",
        http=lambda: httpx.AsyncClient(transport=transport),
    )


async def published(pubsub: Any, tries: int = 5) -> list[dict[str, str]]:
    """Every message on the subscription. ``get_message`` returns None for the subscribe
    confirmation too, so one None does not mean the channel is quiet."""
    out: list[dict[str, str]] = []
    for _ in range(tries):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if message is not None:
            out.append(json.loads(message["data"]))
    return out


async def test_streams_are_read_from_a_wall_time(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.xadd(EVENTS_STREAM, {"event": "old"}, id="1000-0")
    await redis.xadd(EVENTS_STREAM, {"event": "new"}, id="5000-0")
    await redis.xadd(NOTIFICATION_DISPATCH_STREAM, {"notification": "n"}, id="6000-0")
    await redis.rpush(DEFERRED_NOTIFICATIONS_KEY, "a", "b")
    b = bus(redis)
    assert [(e.wall, e.data) for e in await b.events(2.0)] == [(5.0, {"event": "new"})]
    assert [e.wall for e in await b.notifications(0.0)] == [6.0]
    assert await b.deferred() == ["a", "b"]


async def test_a_window_keeps_the_starts_own_millisecond(redis: fakeredis.FakeAsyncRedis) -> None:
    """Stream ids are whole milliseconds; the collectors keep the same one (one rule)."""
    await redis.xadd(EVENTS_STREAM, {"event": "ms before"}, id="4999-0")
    await redis.xadd(EVENTS_STREAM, {"event": "same ms"}, id="5000-0")
    assert [e.data["event"] for e in await bus(redis).events(5.0004)] == ["same ms"]


async def test_wait_for_event_blocks_until_a_wanted_entry_lands(
    redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = 0

    async def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        reads += 1
        return await read(*args, **kwargs)

    monkeypatch.setattr("evals.harness.bus.read", counted)

    async def alfred_writes() -> None:
        await asyncio.sleep(0.1)
        await redis.xadd(EVENTS_STREAM, {"event": "other"})
        await asyncio.sleep(0.1)
        await redis.xadd(EVENTS_STREAM, {"event": "wanted"})

    since = time.time()
    writing = asyncio.create_task(alfred_writes())
    t0 = time.monotonic()
    assert await bus(redis).wait_for_event(since, 5, lambda e: e.data["event"] == "wanted")
    await writing
    assert 0.2 <= time.monotonic() - t0 < 2
    assert reads == 2  # one blocking read per entry, not a poll


async def test_wait_for_event_reads_only_the_window_and_times_out(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.xadd(EVENTS_STREAM, {"event": "ms before"}, id="4999-0")
    b = bus(redis)
    t0 = time.monotonic()
    assert not await b.wait_for_event(5.0004, 0.2, lambda e: True)
    assert time.monotonic() - t0 >= 0.2
    # Already in the window when the wait starts: seen at once, even with no time to wait.
    await redis.xadd(EVENTS_STREAM, {"event": "same ms"}, id="5000-0")
    assert await b.wait_for_event(
        5.0004, 0, lambda e: (e.wall, e.data) == (5.0, {"event": "same ms"})
    )


async def test_advance_pulls_run_at_to_now_and_tells_the_engine(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    run_at = "2026-10-08T20:00:00-06:00"
    row = {"trigger_id": "t1", "name": "Laundry", "conditions": {"run_at": run_at}}
    await redis.hset(TRIGGERS_KEY, "t1", json.dumps(row))
    await redis.hset(TRIGGERS_KEY, "cron", json.dumps({"conditions": {"cron": "0 7 * * *"}}))
    pubsub = redis.pubsub()
    await pubsub.subscribe(TRIGGERS_CHANGED_CHANNEL)
    now = at(18)
    b = bus(redis)
    assert await b.advance_trigger("t1", now)
    stored = json.loads(await redis.hget(TRIGGERS_KEY, "t1"))
    assert stored["conditions"]["run_at"] == now.isoformat() and stored["name"] == "Laundry"
    assert not await b.advance_trigger("cron", now)  # no run_at to bring forward
    assert not await b.advance_trigger("gone", now)
    assert await published(pubsub) == [{"op": "saved", "trigger_id": "t1"}]
    await pubsub.aclose()


async def test_delete_triggers_removes_only_rows_that_exist_and_says_so(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.hset(TRIGGERS_KEY, mapping={"t1": "{}", "keep": "{}"})
    pubsub = redis.pubsub()
    await pubsub.subscribe(TRIGGERS_CHANGED_CHANNEL)
    await bus(redis).delete_triggers(["t1", "already-fired"])
    assert await redis.hkeys(TRIGGERS_KEY) == [b"keep"]
    assert await published(pubsub) == [{"op": "deleted", "trigger_id": "t1"}]
    await pubsub.aclose()


async def test_user_timezone_is_set_restored_and_validated(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe(TRIGGERS_CHANGED_CHANNEL)
    b = bus(redis)
    assert await b.user_timezone() is None
    await b.set_user_timezone("Etc/GMT-7")
    assert await b.user_timezone() == "Etc/GMT-7"
    assert await published(pubsub) == [{"op": "tz-changed"}]
    await b.set_user_timezone(None)
    assert await redis.get(USER_TIMEZONE_KEY) is None
    # The engine re-arms its cron alarms on the env zone Alfred now falls back to.
    assert await published(pubsub) == [{"op": "tz-changed"}]
    await b.set_user_timezone(None)  # nothing was stored, so nothing changed
    with pytest.raises(BusError, match="Mars/Base"):
        await b.set_user_timezone("Mars/Base")
    assert await published(pubsub) == []
    await pubsub.aclose()


async def test_set_dnd_posts_with_a_session_that_lives_only_for_the_call(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    seen: list[tuple[str, object, bytes | None, int]] = []

    async def admin(request: httpx.Request) -> httpx.Response:
        session_id = request.headers["cookie"].removeprefix("alfred_auth=")
        key = f"{AUTH_SESSION_PREFIX}{session_id}"
        session = await redis.hgetall(key)
        content = json.loads(request.content)
        seen.append(
            (str(request.url), content, session.get(b"authenticated"), await redis.ttl(key))
        )
        return httpx.Response(200, json={"active": True})

    await bus(redis, admin).set_dnd(True)
    [(url, content, authenticated, ttl)] = seen
    assert (url, content, authenticated) == (
        "http://alfred.test/api/admin/dnd",
        {"active": True},
        b"1",
    )
    assert 0 < ttl <= 300  # the backstop, should the delete after the call never run
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


async def test_an_unreachable_admin_api_is_a_bus_error_and_leaves_no_session(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    async def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(BusError, match="connection refused"):
        await bus(redis, unreachable).set_dnd(False)
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


class LostExecReply(fakeredis.FakeAsyncRedis):
    """A Redis whose transactions are applied but whose EXEC reply never arrives."""

    def pipeline(self, transaction: bool = True, shard_hint: str | None = None) -> Any:
        pipe = super().pipeline(transaction=transaction, shard_hint=shard_hint)
        execute = pipe.execute

        async def lost(raise_on_error: bool = True) -> list[Any]:
            await execute(raise_on_error)
            raise redis_exceptions.ConnectionError("EXEC reply lost")

        pipe.execute = lost  # type: ignore[method-assign]
        return pipe


async def test_a_failed_session_write_leaves_no_session_and_makes_no_call() -> None:
    redis = LostExecReply()
    calls: list[httpx.Request] = []

    async def admin(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    with pytest.raises(redis_exceptions.ConnectionError, match="EXEC reply lost"):
        await bus(redis, admin).set_dnd(True)
    assert calls == []
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


async def test_a_refused_dnd_call_is_a_bus_error_and_leaves_no_session(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    async def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Not authenticated"})

    with pytest.raises(BusError, match="401"):
        await bus(redis, refuse).set_dnd(True)
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


async def test_clear_dnd_drops_the_state_and_what_it_held(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.set(DND_STATE_KEY, json.dumps({"active": True}))
    await redis.rpush(DEFERRED_NOTIFICATIONS_KEY, "held")
    await bus(redis).clear_dnd()
    assert await redis.exists(DND_STATE_KEY, DEFERRED_NOTIFICATIONS_KEY) == 0


async def test_reflex_tools_are_the_registrys_reflex_audience(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    manifest = {
        "features": [
            {
                "name": "home",
                "tools": [
                    {"name": "home.light_turn_on", "audience": "reflex"},
                    {"name": "home.lock_unlock"},
                ],
            }
        ]
    }
    await redis.hset(ToolRegistry.REGISTRY_KEY, "home-service", json.dumps(manifest))
    assert [t.name for t in await bus(redis).reflex_tools()] == ["home.light_turn_on"]
