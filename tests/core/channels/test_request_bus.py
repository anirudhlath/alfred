"""publish_and_wait — shared request/response bus helper."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import fakeredis

from bus.schemas.events import AlfredResponse, UserRequest
from core.channels.request_bus import FOLLOWUP_TTL_S, LATE_REPLY, publish_and_wait
from shared.streams import FOLLOWUP_KEY, USER_REQUESTS_STREAM, USER_RESPONSES_STREAM


def _request(session_id: str = "sat-kitchen") -> UserRequest:
    return UserRequest(
        source="satellite",
        channel="satellite",
        session_id=session_id,
        identity_claim="sir",
        content_type="audio",
        content="hello",
    )


async def test_publishes_request_and_returns_matching_response() -> None:
    resp = AlfredResponse(
        source="conscious-engine", channel="satellite", session_id="sat-kitchen", text="Hello sir."
    )
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])
    redis.xread = AsyncMock(
        return_value=[(b"stream", [(b"1-1", {b"event": resp.model_dump_json().encode()})])]
    )

    result = await publish_and_wait(redis, _request(), "sat-kitchen", timeout=5.0)

    assert result.text == "Hello sir."
    stream, payload = redis.xadd.call_args.args
    assert stream == USER_REQUESTS_STREAM
    assert json.loads(payload["event"])["session_id"] == "sat-kitchen"


async def test_timeout_returns_fallback_with_request_channel() -> None:
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])

    async def _no_entries(*args: Any, **kwargs: Any) -> list[Any]:
        await asyncio.sleep(0.01)
        return []

    redis.xread = AsyncMock(side_effect=_no_entries)

    result = await publish_and_wait(redis, _request(), "sat-kitchen", timeout=0.05)

    assert result.channel == "satellite"  # not hardcoded web_pwa
    assert result.session_id == "sat-kitchen"


async def test_skips_responses_for_other_sessions() -> None:
    other = AlfredResponse(
        source="conscious-engine", channel="web_pwa", session_id="other", text="nope"
    )
    mine = AlfredResponse(
        source="conscious-engine", channel="satellite", session_id="sat-kitchen", text="yes"
    )
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])
    redis.xread = AsyncMock(
        return_value=[
            (
                b"stream",
                [
                    (b"1-1", {b"event": other.model_dump_json().encode()}),
                    (b"1-2", {b"event": mine.model_dump_json().encode()}),
                ],
            )
        ]
    )

    result = await publish_and_wait(redis, _request(), "sat-kitchen", timeout=5.0)
    assert result.text == "yes"


async def test_anchors_xread_to_response_stream_tail_not_wall_clock() -> None:
    """Regression test for the same-millisecond collision race.

    XREAD's lower bound is exclusive: entries with ID equal to `last_id` are
    NOT returned. A wall-clock-derived last_id can collide with a response
    that lands in the same Redis millisecond, silently dropping it. Anchoring
    to the response stream's real tail (XREVRANGE) instead means the very
    first xread call must use that tail ID verbatim as its offset — not some
    value derived from `time.time()`.
    """
    resp = AlfredResponse(
        source="conscious-engine", channel="satellite", session_id="sat-kitchen", text="fast reply"
    )
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[(b"1000-5", {b"event": b"stale-prior-response"})])
    redis.xread = AsyncMock(
        return_value=[(b"stream", [(b"1000-6", {b"event": resp.model_dump_json().encode()})])]
    )

    result = await publish_and_wait(redis, _request(), "sat-kitchen", timeout=5.0)

    assert result.text == "fast reply"
    first_call = redis.xread.call_args_list[0]
    streams_arg = first_call.args[0]
    offset = streams_arg[USER_RESPONSES_STREAM]
    assert offset in (b"1000-5", "1000-5")


async def test_empty_response_stream_anchors_to_zero() -> None:
    """With no prior history on the responses stream, XREVRANGE returns
    nothing, so the scan must start from the very beginning ("0-0")."""
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])

    async def _no_entries(*args: Any, **kwargs: Any) -> list[Any]:
        await asyncio.sleep(0.01)
        return []

    redis.xread = AsyncMock(side_effect=_no_entries)

    await publish_and_wait(redis, _request(), "sat-kitchen", timeout=0.05)

    first_call = redis.xread.call_args_list[0]
    streams_arg = first_call.args[0]
    offset = streams_arg[USER_RESPONSES_STREAM]
    assert offset == "0-0"


async def test_a_late_answer_to_an_earlier_request_is_not_this_requests_answer() -> None:
    """The final review's I3: request A's redelivered answer lands while the channel waits
    for request B, in the same session; B must get its own answer, not A's."""
    first, second = _request(), _request()
    late = AlfredResponse(
        source="conscious-engine",
        channel="satellite",
        session_id="sat-kitchen",
        text="answer to A",
        in_reply_to=first.event_id,
    )
    mine = AlfredResponse(
        source="conscious-engine",
        channel="satellite",
        session_id="sat-kitchen",
        text="answer to B",
        in_reply_to=second.event_id,
    )
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])
    redis.xread = AsyncMock(
        return_value=[
            (
                b"stream",
                [
                    (b"1-1", {b"event": late.model_dump_json().encode()}),
                    (b"1-2", {b"event": mine.model_dump_json().encode()}),
                ],
            )
        ]
    )

    result = await publish_and_wait(redis, second, "sat-kitchen", timeout=5.0)

    assert result.text == "answer to B"


async def test_a_response_without_a_request_id_still_matches_by_session() -> None:
    """Compatibility: a producer that does not set in_reply_to is matched as before."""
    resp = AlfredResponse(
        source="conscious-engine", channel="satellite", session_id="sat-kitchen", text="old"
    )
    assert resp.in_reply_to is None
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])
    redis.xread = AsyncMock(
        return_value=[(b"stream", [(b"1-1", {b"event": resp.model_dump_json().encode()})])]
    )

    result = await publish_and_wait(redis, _request(), "sat-kitchen", timeout=5.0)

    assert result.text == "old"


async def test_the_timeout_fallback_names_the_request() -> None:
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])

    async def _no_entries(*args: Any, **kwargs: Any) -> list[Any]:
        await asyncio.sleep(0.01)
        return []

    redis.xread = AsyncMock(side_effect=_no_entries)
    request = _request()

    result = await publish_and_wait(redis, request, "sat-kitchen", timeout=0.05)

    assert result.in_reply_to == request.event_id


class _Bus(fakeredis.FakeAsyncRedis):
    """A real stream store, with a stand-in for the conscious engine's timing.

    ``on_request`` runs when the channel publishes its request, and ``on_give_up`` when it
    marks its wait late: the two moments an answer lands in these tests. A blocking read
    answers at once (fakeredis would sit out its whole second past a 50 ms timeout), so the
    wait loop simply spins until its timeout.
    """

    on_request: Callable[[], Awaitable[None]] | None = None
    on_give_up: Callable[[], Awaitable[None]] | None = None

    async def xread(self, streams: Any, count: Any = None, block: Any = None, **kw: Any) -> Any:
        return await super().xread(streams, count=count, **kw)

    async def xadd(self, name: Any, fields: Any, *args: Any, **kwargs: Any) -> Any:
        entry_id = await super().xadd(name, fields, *args, **kwargs)
        if name == USER_REQUESTS_STREAM and self.on_request is not None:
            await self.on_request()
        return entry_id

    async def set(self, name: Any, value: Any, *args: Any, **kwargs: Any) -> Any:
        result = await super().set(name, value, *args, **kwargs)
        if kwargs.get("xx") and self.on_give_up is not None:
            await self.on_give_up()
        return result


def _answer(request: UserRequest, text: str = "Done, sir.", **kw: Any) -> AlfredResponse:
    return AlfredResponse(
        source="conscious-engine",
        channel=request.channel,
        session_id=request.session_id,
        text=text,
        in_reply_to=request.event_id,
        **kw,
    )


async def _publish(bus: _Bus, response: AlfredResponse) -> None:
    await bus.xadd(USER_RESPONSES_STREAM, {"event": response.model_dump_json()})


async def test_an_in_time_answer_clears_the_registration() -> None:
    bus, request = _Bus(), _request()
    key = FOLLOWUP_KEY.format(request.event_id)
    seen_at_publish: list[bytes | None] = []

    async def engine() -> None:
        seen_at_publish.append(await bus.get(key))
        await _publish(bus, _answer(request))

    bus.on_request = engine

    result = await publish_and_wait(bus, request, "sat-kitchen", timeout=5.0)

    assert result.text == "Done, sir."
    assert seen_at_publish == [b"waiting"]  # registered before the request went out
    assert await bus.exists(key) == 0


async def test_a_timeout_marks_the_registration_late_and_says_so() -> None:
    bus, request = _Bus(), _request()
    key = FOLLOWUP_KEY.format(request.event_id)

    result = await publish_and_wait(bus, request, "sat-kitchen", timeout=0.05)

    assert result.text == LATE_REPLY
    assert result.in_reply_to == request.event_id
    assert not result.followup
    assert await bus.get(key) == b"late"
    assert 0 < await bus.ttl(key) <= FOLLOWUP_TTL_S == 600


async def test_an_answer_landing_at_the_timeout_is_returned_once() -> None:
    """The answer lands as the channel gives up: its final read returns it, and the key
    is gone, so the follow-up worker skips the same entry."""
    bus, request = _Bus(), _request()
    key = FOLLOWUP_KEY.format(request.event_id)

    async def answer_now() -> None:
        await _publish(bus, _answer(request, "Just in time, sir."))

    bus.on_give_up = answer_now

    result = await publish_and_wait(bus, request, "sat-kitchen", timeout=0.05)

    assert result.text == "Just in time, sir."
    assert await bus.exists(key) == 0


async def test_an_answer_the_worker_claimed_first_is_not_returned_again() -> None:
    """The worker won the DEL for the answer the final read also finds: the channel gives
    the fallback, so the answer is shown once, by the worker."""
    bus, request = _Bus(), _request()
    key = FOLLOWUP_KEY.format(request.event_id)

    async def worker_wins() -> None:
        await _publish(bus, _answer(request, "Delivered by the worker."))
        await bus.delete(key)

    bus.on_give_up = worker_wins

    result = await publish_and_wait(bus, request, "sat-kitchen", timeout=0.05)

    assert result.text == LATE_REPLY


async def test_a_followup_never_answers_a_waiting_request() -> None:
    bus, request = _Bus(), _request()

    async def engine() -> None:
        await _publish(bus, _answer(request, "A resumed run's reply.", followup=True))
        await _publish(bus, _answer(request, "The answer."))

    bus.on_request = engine

    result = await publish_and_wait(bus, request, "sat-kitchen", timeout=5.0)

    assert result.text == "The answer."
