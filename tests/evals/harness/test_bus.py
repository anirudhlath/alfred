from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest
from redis import exceptions as redis_exceptions

from bus.schemas.events import (
    ActionRequest,
    AlfredResponse,
    ParkedAction,
    ParkedCall,
    ParkedRun,
    ReflexOutcome,
    ReflexProposal,
    StateChangedEvent,
)
from core.reflex.decision_log import DecisionLog
from core.reflex.outcome import Decided
from core.reflex.tool_registry import ToolRegistry
from evals.harness.bus import BusError, ContainerBus, zone_for_hour
from shared.redis_streams import read
from shared.streams import (
    ACTIONS_STREAM,
    AUTH_SESSION_PREFIX,
    DEFERRED_NOTIFICATIONS_KEY,
    DND_STATE_KEY,
    EVENTS_STREAM,
    NOTIFICATION_DISPATCH_STREAM,
    PENDING_PREFIX,
    REFLEX_ANSWERED_PREFIX,
    REFLEX_ANSWERS_STREAM,
    REFLEX_ASK_OPEN_KEY,
    REFLEX_ASKED_PREFIX,
    REFLEX_ASKS_PREFIX,
    REFLEX_DECISION_LOG_STREAM,
    REFLEX_PERSON_CHANGES_KEY,
    REFLEX_SETTINGS_KEY,
    TRIGGERS_CHANGED_CHANNEL,
    TRIGGERS_KEY,
    USER_RESPONSES_STREAM,
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
    await redis.xadd(ACTIONS_STREAM, {"event": "old action"}, id="1500-0")
    await redis.xadd(ACTIONS_STREAM, {"event": "action"}, id="7000-0")
    await redis.rpush(DEFERRED_NOTIFICATIONS_KEY, "a", "b")
    b = bus(redis)
    assert [(e.wall, e.data) for e in await b.events(2.0)] == [(5.0, {"event": "new"})]
    assert [e.wall for e in await b.notifications(0.0)] == [6.0]
    assert [(e.wall, e.data) for e in await b.actions(2.0)] == [(7.0, {"event": "action"})]
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


async def test_wait_for_event_wakes_on_alfred_actions_too(redis: fakeredis.FakeAsyncRedis) -> None:
    """A trigger with an action fires onto alfred:actions, not alfred:events."""
    await redis.xadd(ACTIONS_STREAM, {"event": "ms before"}, id="4999-0")
    b = bus(redis)
    assert not await b.wait_for_event(5.0004, 0.1, lambda e: True)  # the window rule holds

    async def the_engine_acts() -> None:
        await asyncio.sleep(0.1)
        await redis.xadd(EVENTS_STREAM, {"event": "other"})
        await asyncio.sleep(0.1)
        await redis.xadd(ACTIONS_STREAM, {"event": "action"})

    since = time.time()
    acting = asyncio.create_task(the_engine_acts())
    t0 = time.monotonic()
    assert await b.wait_for_event(since, 5, lambda e: e.data["event"] == "action")
    await acting
    assert 0.2 <= time.monotonic() - t0 < 2


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


FOUND = {"mode": "shadow", "ask_tools": ["home.cover_*"], "daily_ask_cap": 3}


class SettingsApi:
    """The admin API's settings route: a GET answers the stored document, a PUT replaces it,
    and each call is made with a live session."""

    def __init__(self, redis: fakeredis.FakeAsyncRedis, *, put_status: int = 200) -> None:
        self.redis, self.put_status = redis, put_status
        self.document = dict(FOUND)
        self.calls: list[tuple[str, str, object, bytes | None]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        session_id = request.headers["cookie"].removeprefix("alfred_auth=")
        session = await self.redis.hgetall(f"{AUTH_SESSION_PREFIX}{session_id}")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body, session.get(b"authenticated")))
        if request.method == "PUT":
            if self.put_status != 200:
                return httpx.Response(self.put_status, json={"detail": "refused"})
            self.document = body
        return httpx.Response(200, json=self.document)


async def test_set_reflex_settings_lays_the_change_over_the_document_it_found(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    api = SettingsApi(redis)
    found = await bus(redis, api).set_reflex_settings(
        {"mode": "ask", "ask_tools": ["home.light_*"]}
    )
    assert found == FOUND
    path = "/api/admin/reflex/settings"
    assert api.calls == [
        ("GET", path, None, b"1"),
        ("PUT", path, {**FOUND, "mode": "ask", "ask_tools": ["home.light_*"]}, b"1"),
    ]
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []  # each session lived for its call


async def test_a_field_the_change_does_not_name_keeps_its_value(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    api = SettingsApi(redis)
    await bus(redis, api).set_reflex_settings({"mode": "ask"})
    assert api.document == {**FOUND, "mode": "ask"}


async def test_a_quiet_hours_change_goes_out_in_the_put_beside_the_rest_of_the_document(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    api = SettingsApi(redis)
    await bus(redis, api).set_reflex_settings(
        {"mode": "ask", "quiet_hours_start": "04:00", "quiet_hours_end": "05:00"}
    )
    assert api.document == {
        **FOUND,
        "mode": "ask",
        "quiet_hours_start": "04:00",
        "quiet_hours_end": "05:00",
    }


@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_an_unreachable_settings_route_names_the_method_that_failed(
    redis: fakeredis.FakeAsyncRedis, method: str
) -> None:
    api = SettingsApi(redis)

    async def unreachable(request: httpx.Request) -> httpx.Response:
        if request.method != method:
            return await api(request)
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(BusError, match=f"{method} /api/admin/reflex/settings failed: connection"):
        await bus(redis, unreachable).set_reflex_settings({"mode": "ask"})
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


async def test_restore_reflex_settings_puts_the_whole_document_back(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    api = SettingsApi(redis)
    await bus(redis, api).restore_reflex_settings(FOUND)
    assert api.calls == [("PUT", "/api/admin/reflex/settings", FOUND, b"1")]


@pytest.mark.parametrize("status", [401, 422, 503])
async def test_a_refused_settings_call_is_a_bus_error_and_leaves_no_session(
    redis: fakeredis.FakeAsyncRedis, status: int
) -> None:
    with pytest.raises(BusError, match=f"PUT /api/admin/reflex/settings answered {status}"):
        await bus(redis, SettingsApi(redis, put_status=status)).set_reflex_settings({"mode": "ask"})
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


@pytest.mark.parametrize("body", ["not json", "[1]"])
async def test_a_settings_reply_that_is_not_a_document_is_a_bus_error(
    redis: fakeredis.FakeAsyncRedis, body: str
) -> None:
    async def odd(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    with pytest.raises(BusError, match="GET /api/admin/reflex/settings answered"):
        await bus(redis, odd).set_reflex_settings({"mode": "ask"})


async def test_the_settings_document_is_not_written_by_the_harness_directly(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """The change goes through the admin API, which validates it, never to the key."""
    await bus(redis, SettingsApi(redis)).set_reflex_settings({"mode": "ask"})
    assert not await redis.exists(REFLEX_SETTINGS_KEY)


class NoKeys(fakeredis.FakeAsyncRedis):
    """KEYS blocks a real Redis while it walks every key: the harness must SCAN."""

    async def keys(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("KEYS must not be used")


async def test_clearing_reflex_asks_drops_the_asking_state_and_nothing_else() -> None:
    redis = NoKeys()
    await redis.set(REFLEX_ASK_OPEN_KEY, "{}")
    for prefix in (REFLEX_ASKED_PREFIX, REFLEX_ASKS_PREFIX, REFLEX_ANSWERED_PREFIX):
        await redis.set(f"{prefix}a", "1")
        await redis.set(f"{prefix}b", "1")
    await redis.xadd(REFLEX_ANSWERS_STREAM, {"answer": "yes"})
    await redis.set(REFLEX_SETTINGS_KEY, "{}")
    await redis.set("alfred:reflex:unrelated", "kept")
    await bus(redis).clear_reflex_asks()
    left = [k.decode() async for k in redis.scan_iter(match="*")]
    assert sorted(left) == sorted(
        [REFLEX_ANSWERS_STREAM, REFLEX_SETTINGS_KEY, "alfred:reflex:unrelated"]
    )
    await bus(redis).clear_reflex_asks()  # nothing to clear is fine


async def test_clearing_reflex_asks_reaches_past_one_scan_page() -> None:
    redis = NoKeys()
    for i in range(350):
        await redis.set(f"{REFLEX_ASKED_PREFIX}{i}", "1")
    await bus(redis).clear_reflex_asks()
    assert [k async for k in redis.scan_iter(match=f"{REFLEX_ASKED_PREFIX}*")] == []


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


class Frozen(fakeredis.FakeAsyncRedis):
    """A container frozen with its socket still open: every command waits for ever."""

    async def execute_command(self, *args: Any, **options: Any) -> Any:
        await asyncio.Event().wait()

    def pipeline(self, transaction: bool = True, shard_hint: Any = None) -> Any:
        pipe = super().pipeline(transaction=transaction, shard_hint=shard_hint)
        pipe.execute = self.execute_command  # type: ignore[method-assign]
        return pipe


FROZEN_CALLS: dict[str, Callable[[ContainerBus], Awaitable[object]]] = {
    "pending_actions": lambda b: b.pending_actions(),
    "delete_pending_actions": lambda b: b.delete_pending_actions(["a"]),
    "events": lambda b: b.events(0),
    "actions": lambda b: b.actions(0),
    "notifications": lambda b: b.notifications(0),
    "deferred": lambda b: b.deferred(),
    "reflex_tools": lambda b: b.reflex_tools(),
    "advance_trigger": lambda b: b.advance_trigger("t1", at(12)),
    "delete_triggers": lambda b: b.delete_triggers(["t1"]),
    "user_timezone": lambda b: b.user_timezone(),
    "set_user_timezone": lambda b: b.set_user_timezone("UTC"),
    "clear_user_timezone": lambda b: b.set_user_timezone(None),
    "set_dnd": lambda b: b.set_dnd(True),
    "clear_dnd": lambda b: b.clear_dnd(),
    "set_reflex_settings": lambda b: b.set_reflex_settings({"mode": "ask"}),
    "restore_reflex_settings": lambda b: b.restore_reflex_settings({"mode": "shadow"}),
    "clear_reflex_asks": lambda b: b.clear_reflex_asks(),
    "wait_for_event": lambda b: b.wait_for_event(time.time(), 0.2, lambda e: True),
    "decision_records": lambda b: b.decision_records(at(12)),
    "wait_for_decisions": lambda b: b.wait_for_decisions(at(12), 0.2, lambda records: True),
    "clear_person_changes": lambda b: b.clear_person_changes(),
    "decide_pending": lambda b: b.decide_pending("a", "confirm"),
    "responses_tail": lambda b: b.responses_tail(),
    "followup": lambda b: b.followup("r1", since_id="0-0", timeout_s=0.2),
}


@pytest.mark.parametrize("call", list(FROZEN_CALLS))
async def test_a_frozen_redis_is_a_bus_error_not_a_hang(call: str) -> None:
    frozen = ContainerBus(
        lambda: Frozen(),
        lambda: "http://alfred.test",
        http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(ok)),
        timeout_s=0.1,
    )
    started = time.monotonic()
    with pytest.raises(BusError, match="did not answer"):
        await asyncio.wait_for(FROZEN_CALLS[call](frozen), timeout=5)
    elapsed = time.monotonic() - started
    assert elapsed < 1
    if call in ("wait_for_event", "followup", "wait_for_decisions"):
        # its own wait (0.2 s), then the call's bound
        assert elapsed >= 0.3


def _stored(action: ActionRequest) -> str:
    return ParkedAction(action=action).model_dump_json()


async def test_pending_reads_preserve_real_ids_and_cleanup_only_requested_keys(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    action = ActionRequest(
        source="conscious-engine",
        request_id="a",
        target_service="home-service",
        tool_name="home.lock_unlock",
    )
    await redis.set(f"{PENDING_PREFIX}a", _stored(action), ex=300)
    await redis.set(
        f"{PENDING_PREFIX}keep",
        _stored(action.model_copy(update={"request_id": "keep"})),
        ex=300,
    )
    await redis.set("unrelated", "not json")
    b = bus(redis)
    assert {a.request_id for a in await b.pending_actions()} == {"a", "keep"}
    await b.delete_pending_actions(["a", "expired"])
    assert [a.request_id for a in await b.pending_actions()] == ["keep"]
    assert await redis.get("unrelated") == b"not json"


async def test_a_parked_run_reads_as_one_action_request_per_call_under_its_record_id(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    run = ParkedRun(
        approval_id="appr-1",
        session_id="sess-1",
        channel="web_pwa",
        identity={"user": "sir", "method": "passkey"},
        request_event_id="req-1",
        created_at=at(12),
        content_type="text",
        tz_name="UTC",
        summary="Unlock both doors.",
        calls=[
            ParkedCall(
                tool_call_id=f"call-{n}",
                tool_name="home.lock_unlock",
                target_service="home-service",
                parameters={"entity_id": f"lock.door_{n}"},
            )
            for n in range(2)
        ],
        messages="[]",
    )
    await redis.set(f"{PENDING_PREFIX}appr-1", run.model_dump_json(), ex=300)
    b = bus(redis)

    actions = await b.pending_actions()

    assert [(a.request_id, a.parameters["entity_id"]) for a in actions] == [
        ("appr-1", "lock.door_0"),
        ("appr-1", "lock.door_1"),
    ]
    assert all(a.confirmed is False for a in actions)
    await b.delete_pending_actions(["appr-1", "appr-1"])  # a repeated id is deleted once
    assert await b.pending_actions() == []


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "{}",
        # a record whose kind the store does not know
        '{"kind": "mystery", "action": {}}',
        # an action whose fields do not validate
        '{"kind": "action", "action": {"source": 1, "target_service": "x", "tool_name": "x"}}',
    ],
)
async def test_malformed_pending_is_a_bus_error_not_silently_omitted(
    redis: fakeredis.FakeAsyncRedis, raw: str
) -> None:
    await redis.set(f"{PENDING_PREFIX}bad", raw)
    with pytest.raises(BusError, match="pending"):
        await bus(redis).pending_actions()


async def test_pending_key_id_mismatch_is_a_bus_error(redis: fakeredis.FakeAsyncRedis) -> None:
    action = ActionRequest(source="x", request_id="other", target_service="x", tool_name="x")
    await redis.set(f"{PENDING_PREFIX}a", _stored(action))
    with pytest.raises(BusError, match="pending"):
        await bus(redis).pending_actions()


async def test_pending_key_expiring_between_scan_and_get_is_valid_empty_evidence(
    redis: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def expired(*args: Any, **kwargs: Any) -> Any:
        yield f"{PENDING_PREFIX}expired".encode()

    monkeypatch.setattr(redis, "scan_iter", expired)
    assert await bus(redis).pending_actions() == []


async def test_decide_pending_posts_the_decision_with_a_session_that_lives_only_for_the_call(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    seen: list[tuple[str, str, bytes | None, int]] = []

    async def web(request: httpx.Request) -> httpx.Response:
        session_id = request.headers["cookie"].removeprefix("alfred_auth=")
        key = f"{AUTH_SESSION_PREFIX}{session_id}"
        session = await redis.hgetall(key)
        seen.append(
            (request.method, str(request.url), session.get(b"authenticated"), await redis.ttl(key))
        )
        return httpx.Response(200, json={})

    b = bus(redis, web)
    assert await b.decide_pending("abc123", "confirm") == 200
    assert await b.decide_pending("abc123", "deny") == 200
    assert [(m, u, a) for m, u, a, _ in seen] == [
        ("POST", "http://alfred.test/api/actions/abc123/confirm", b"1"),
        ("POST", "http://alfred.test/api/actions/abc123/deny", b"1"),
    ]
    assert all(0 < ttl <= 300 for *_, ttl in seen)
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_decide_pending_returns_a_refusal_for_the_driver_to_record(
    redis: fakeredis.FakeAsyncRedis, status: int
) -> None:
    async def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "no"})

    assert await bus(redis, refuse).decide_pending("gone", "confirm") == status
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


@pytest.mark.parametrize("status", [401, 403, 405, 422, 502])
async def test_decide_pending_raises_on_a_status_the_product_never_answers_with(
    redis: fakeredis.FakeAsyncRedis, status: int
) -> None:
    """A rejected session or a changed contract is the harness's fault: recorded as the
    product's answer it would score a golden as a regression."""

    async def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "no"})

    with pytest.raises(BusError, match=f"/api/actions/gone/confirm answered {status}: "):
        await bus(redis, refuse).decide_pending("gone", "confirm")
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


async def test_an_unreachable_web_channel_is_a_bus_error_and_leaves_no_session(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    async def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(BusError, match="/api/actions/a/deny failed: connection refused"):
        await bus(redis, unreachable).decide_pending("a", "deny")
    assert await redis.keys(f"{AUTH_SESSION_PREFIX}*") == []


def response(
    in_reply_to: str | None, *, followup: bool = True, text: str = "Done, sir."
) -> AlfredResponse:
    return AlfredResponse(
        source="conscious-engine",
        channel="web_pwa",
        session_id="s1",
        text=text,
        in_reply_to=in_reply_to,
        followup=followup,
    )


async def publish(redis: fakeredis.FakeAsyncRedis, resp: AlfredResponse) -> str:
    entry_id = await redis.xadd(USER_RESPONSES_STREAM, {"event": resp.model_dump_json()})
    return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)


async def test_the_responses_tail_is_the_last_entry_or_the_start(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    b = bus(redis)
    assert await b.responses_tail() == "0-0"
    await publish(redis, response("r0"))
    last = await publish(redis, response("r1"))
    assert await b.responses_tail() == last


async def test_followup_is_the_answer_to_that_request_after_the_tail(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    b = bus(redis)
    await publish(redis, response("r1", text="Before the decision."))
    tail = await b.responses_tail()
    await publish(redis, response("r1", followup=False, text="Not a follow-up."))
    await publish(redis, response("r2", text="Another request."))
    await publish(redis, response(None, text="Nobody's."))
    await publish(redis, response("r1", text="The outcome."))
    await publish(redis, response("r1", text="A second one."))
    found = await b.followup("r1", since_id=tail, timeout_s=1)
    assert found is not None and found.text == "The outcome." and found.followup


async def test_followup_blocks_until_the_answer_lands(redis: fakeredis.FakeAsyncRedis) -> None:
    b = bus(redis)
    tail = await b.responses_tail()

    async def the_engine_resumes() -> None:
        await asyncio.sleep(0.05)
        await publish(redis, response("r2"))
        await asyncio.sleep(0.05)
        await publish(redis, response("r1", text="Late."))

    resumed = asyncio.create_task(the_engine_resumes())
    started = time.monotonic()
    found = await b.followup("r1", since_id=tail, timeout_s=5)
    assert found is not None and found.text == "Late."
    assert time.monotonic() - started < 2
    await resumed


async def test_followup_gives_up_after_its_timeout(redis: fakeredis.FakeAsyncRedis) -> None:
    b = bus(redis)
    await publish(redis, response("r2"))
    started = time.monotonic()
    assert await b.followup("r1", since_id="0-0", timeout_s=0.2) is None
    assert 0.2 <= time.monotonic() - started < 2


async def test_followup_passes_over_an_entry_that_holds_no_response(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.xadd(USER_RESPONSES_STREAM, {"other": "x"})
    await publish(redis, response("r1"))
    found = await bus(redis).followup("r1", since_id="0-0", timeout_s=1)
    assert found is not None and found.in_reply_to == "r1"


async def test_an_unreadable_response_is_a_bus_error(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.xadd(USER_RESPONSES_STREAM, {"event": "{not json"})
    with pytest.raises(BusError, match=USER_RESPONSES_STREAM):
        await bus(redis).followup("r1", since_id="0-0", timeout_s=1)


def state_change(
    entity_id: str = "light.living_room_lamp", new_state: str = "off"
) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state="on",
        new_state=new_state,
    )


async def log_decision(
    redis: fakeredis.FakeAsyncRedis,
    kind: str = "none",
    *,
    entity_id: str = "light.living_room_lamp",
    when: datetime | None = None,
) -> None:
    """Reflex writes one decision to its log, as the runner does (``DecisionLog.record``)."""
    decided = Decided(
        proposal=ReflexProposal(decision="none"),
        outcome=ReflexOutcome.model_validate({"kind": kind}),
        prompt="the prompt",
        recent={},
    )
    log = DecisionLog(redis, clock=lambda: when or datetime.now(UTC))
    await log.record("state_change", state_change(entity_id), decided)


async def test_decision_records_are_what_reflex_logged_from_the_start_of_the_window(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    started = datetime.now(UTC)
    await log_decision(redis, "shadow", when=started - timedelta(minutes=5))  # an earlier sample's
    await log_decision(redis, "would_reverse")
    records = await bus(redis).decision_records(started)
    assert [(r.event["entity_id"], r.outcome and r.outcome.kind) for r in records] == [
        ("light.living_room_lamp", "would_reverse")
    ]
    assert await bus(redis).decision_records(started + timedelta(minutes=1)) == []


async def test_wait_for_decisions_returns_at_once_when_the_log_already_holds_enough(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    started = datetime.now(UTC) - timedelta(seconds=1)
    await log_decision(redis)
    t0 = time.monotonic()
    records = await bus(redis).wait_for_decisions(started, 5, lambda rs: len(rs) >= 1)
    assert len(records) == 1 and time.monotonic() - t0 < 1


async def test_wait_for_decisions_blocks_until_enough_have_landed(
    redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reflex logs a decision just after the proxy records the call it came from: the wait
    wakes as the record lands, one blocking read per record, not a poll."""
    reads = 0

    async def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        reads += 1
        return await read(*args, **kwargs)

    monkeypatch.setattr("evals.harness.bus.read", counted)

    async def reflex_logs() -> None:
        await asyncio.sleep(0.1)
        await log_decision(redis, "none")
        await asyncio.sleep(0.1)
        await log_decision(redis, "would_reverse")

    started = datetime.now(UTC) - timedelta(seconds=1)
    logging = asyncio.create_task(reflex_logs())
    t0 = time.monotonic()
    records = await bus(redis).wait_for_decisions(started, 5, lambda rs: len(rs) >= 2)
    await logging
    assert [r.outcome and r.outcome.kind for r in records] == ["none", "would_reverse"]
    assert 0.2 <= time.monotonic() - t0 < 2
    assert reads == 2


async def test_wait_for_decisions_gives_what_there_is_after_its_timeout(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    started = datetime.now(UTC) - timedelta(seconds=1)
    await log_decision(redis)
    t0 = time.monotonic()
    records = await bus(redis).wait_for_decisions(started, 0.2, lambda rs: len(rs) >= 2)
    assert len(records) == 1 and time.monotonic() - t0 >= 0.2


async def test_a_decision_logged_between_the_read_and_the_block_is_not_missed(
    redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tail is taken before the first read, so a record that lands after that read and
    before the block still ends the wait."""
    started = datetime.now(UTC) - timedelta(seconds=1)
    first = True

    async def lands_before_the_block(*args: Any, **kwargs: Any) -> Any:
        nonlocal first
        if first:
            first = False
            await log_decision(redis)
        return await read(*args, **kwargs)

    monkeypatch.setattr("evals.harness.bus.read", lands_before_the_block)
    t0 = time.monotonic()
    records = await bus(redis).wait_for_decisions(started, 5, lambda rs: len(rs) >= 1)
    assert len(records) == 1 and time.monotonic() - t0 < 1


async def test_clearing_person_changes_drops_reflexs_record_and_nothing_else(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.hset(REFLEX_PERSON_CHANGES_KEY, "light.living_room_lamp", "{}")
    await redis.xadd(REFLEX_DECISION_LOG_STREAM, {"decision": "kept"})
    await redis.set("unrelated", "kept")
    await bus(redis).clear_person_changes()
    assert not await redis.exists(REFLEX_PERSON_CHANGES_KEY)
    assert await redis.xlen(REFLEX_DECISION_LOG_STREAM) == 1
    assert await redis.get("unrelated") == b"kept"
    await bus(redis).clear_person_changes()  # nothing to clear is fine
