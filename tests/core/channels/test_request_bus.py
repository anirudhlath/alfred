"""publish_and_wait — shared request/response bus helper."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

from bus.schemas.events import AlfredResponse, UserRequest
from core.channels.request_bus import publish_and_wait
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM


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
