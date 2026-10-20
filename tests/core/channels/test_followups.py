"""followup_worker — delivers the replies no channel is waiting for any more (#307)."""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis
from loguru import logger

from bus.schemas.events import AlfredResponse
from core.channels.followups import FOLLOWUP_GROUP, followup_worker
from shared.streams import FOLLOWUP_KEY, USER_RESPONSES_STREAM

_SENTINEL = "sentinel"


class _Redis(fakeredis.FakeAsyncRedis):
    """fakeredis answers a blocking read at once, so a worker loop on it never yields and
    starves every other task; this one waits a moment on an empty read, as a server would."""

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> Any:
        entries = await super().xreadgroup(*args, **kwargs)
        if not entries:
            await asyncio.sleep(0.01)
        return entries


class _RacingRedis(_Redis):
    """Hands each read one entry and yields after a GET, so two workers each hold a copy of
    a late answer and both find it late before either deletes the key."""

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["count"] = 1
        return await super().xreadgroup(*args, **kwargs)

    async def get(self, name: Any) -> Any:
        value = await super().get(name)
        await asyncio.sleep(0)
        return value


class _Sink:
    """Records what the worker delivers, raises for ``fail_on``, and stops the workers once
    the sentinel arrives, so a test runs every entry XADDed before it."""

    def __init__(self, shutdown: asyncio.Event, fail_on: str | None = None) -> None:
        self.shutdown = shutdown
        self.fail_on = fail_on
        self.delivered: list[AlfredResponse] = []

    async def __call__(self, response: AlfredResponse) -> None:
        if response.text == _SENTINEL:
            self.shutdown.set()
            return
        if response.text == self.fail_on:
            raise RuntimeError("the socket went away")
        self.delivered.append(response)


def _response(text: str, *, in_reply_to: str | None = "req-1", followup: bool = False) -> str:
    return AlfredResponse(
        source="conscious-engine",
        channel="web_pwa",
        session_id="s1",
        text=text,
        in_reply_to=in_reply_to,
        followup=followup,
    ).model_dump_json()


async def _redis(store: type[_Redis] = _Redis) -> _Redis:
    """A store whose follow-up group exists: a worker run with shutdown already set creates
    the group, at the stream's end, and reads nothing."""
    redis = store()
    stopped = asyncio.Event()
    stopped.set()
    await followup_worker(redis, deliver=_Sink(stopped), consumer="setup", shutdown=stopped)
    return redis


async def _run(
    redis: _Redis, entries: list[dict[str, str | bytes]], *, workers: int = 1, **kw: Any
) -> _Sink:
    """XADD *entries* and the sentinel, then run *workers* consumers until it arrives."""
    sentinel: dict[str, str | bytes] = {"event": _response(_SENTINEL, followup=True)}
    for fields in [*entries, sentinel]:
        await redis.xadd(USER_RESPONSES_STREAM, fields)
    shutdown = asyncio.Event()
    sink = _Sink(shutdown, **kw)
    await asyncio.wait_for(
        asyncio.gather(
            *(
                followup_worker(redis, deliver=sink, consumer=f"worker-{n}", shutdown=shutdown)
                for n in range(workers)
            )
        ),
        timeout=5,
    )
    return sink


async def test_a_late_answer_is_delivered_once() -> None:
    """Two workers race on one late key, each with a copy of its answer, and both read it
    late: the DEL decides, so one delivers."""
    redis = await _redis(_RacingRedis)
    await redis.set(FOLLOWUP_KEY.format("req-1"), "late")

    late = {"event": _response("Late, sir.")}
    sink = await _run(redis, [late, late], workers=2)

    assert [r.text for r in sink.delivered] == ["Late, sir."]
    assert await redis.exists(FOLLOWUP_KEY.format("req-1")) == 0


async def test_an_answer_someone_waits_for_is_skipped() -> None:
    redis = await _redis()
    await redis.set(FOLLOWUP_KEY.format("req-1"), "waiting")

    sink = await _run(
        redis,
        [
            {"event": _response("Still awaited.")},
            {"event": _response("Its registration expired.", in_reply_to="req-gone")},
            {"event": _response("Names no request.", in_reply_to=None)},
        ],
    )

    assert sink.delivered == []
    # Left alone, so the channel's own timeout can still mark it late.
    assert await redis.get(FOLLOWUP_KEY.format("req-1")) == b"waiting"


async def test_a_followup_is_always_delivered() -> None:
    """A resumed run's reply is delivered whatever the key says, and leaves a late key for
    the late answer it belongs to."""
    redis = await _redis()
    await redis.set(FOLLOWUP_KEY.format("req-1"), "late")

    sink = await _run(redis, [{"event": _response("Door says yes.", followup=True)}])

    assert [r.text for r in sink.delivered] == ["Door says yes."]
    assert sink.delivered[0].followup
    assert await redis.get(FOLLOWUP_KEY.format("req-1")) == b"late"


async def test_history_is_not_replayed() -> None:
    """The group starts at the stream's end: a follow-up published before it existed is
    not delivered."""
    redis = _Redis()
    await redis.xadd(USER_RESPONSES_STREAM, {"event": _response("Old news.", followup=True)})
    stopped = asyncio.Event()
    stopped.set()
    await followup_worker(redis, deliver=_Sink(stopped), consumer="setup", shutdown=stopped)

    sink = await _run(redis, [])

    assert sink.delivered == []


async def test_every_entry_is_acked() -> None:
    redis = await _redis()
    await redis.set(FOLLOWUP_KEY.format("req-1"), "late")
    warnings: list[str] = []
    sink_id = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        sink = await _run(
            redis,
            [
                {"event": "not json"},
                {"event": b"\xff\xfe not utf-8"},
                {"other": "field"},
                {"event": _response("Nobody waits.", in_reply_to="req-unknown")},
                {"event": _response("Late, and the socket is gone.")},
                {"event": _response("Delivered.", followup=True)},
            ],
            fail_on="Late, and the socket is gone.",
        )
    finally:
        logger.remove(sink_id)

    assert [r.text for r in sink.delivered] == ["Delivered."]
    pending = await redis.xpending(USER_RESPONSES_STREAM, FOLLOWUP_GROUP)
    assert pending["pending"] == 0
    assert any("the socket went away" in w for w in warnings)
