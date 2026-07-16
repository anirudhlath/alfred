"""publish_and_wait — shared request/response bus helper."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

from bus.schemas.events import AlfredResponse, UserRequest
from core.channels.request_bus import publish_and_wait
from shared.streams import USER_REQUESTS_STREAM


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
