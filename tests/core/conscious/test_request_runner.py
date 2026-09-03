"""Conscious Engine PEL recovery — reclaimed entries must actually be processed.

Regression: the reclaim block called XAUTOCLAIM and then only logged the count.
It never processed or ACKed what it claimed, so the same 4 stuck user requests
were re-claimed and re-logged every 60 seconds indefinitely — 1,440 log lines a
day and no progress. Reclaiming without processing is a slower way of doing
nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import AlfredResponse
from core.conscious.runner import process_request_entry

_REQUEST = (
    '{"event_id":"11111111-1111-1111-1111-111111111111","event_type":"user_request",'
    '"timestamp":"2026-09-03T03:00:00Z","source":"web","channel":"web_pwa",'
    '"session_id":"s1","identity_claim":"sir","content_type":"text",'
    '"content":"turn off the lights"}'
)


def _engine() -> AsyncMock:
    engine = AsyncMock()
    engine.process_request = AsyncMock(
        return_value=AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id="s1", text="Done, sir."
        )
    )
    return engine


@pytest.mark.asyncio
async def test_processes_publishes_and_acks() -> None:
    redis, engine = AsyncMock(), _engine()

    await process_request_entry(
        b"1-0",
        {b"event": _REQUEST.encode()},
        engine=engine,
        redis=redis,
        stream="alfred:user:requests",
        group="conscious-engine",
    )

    engine.process_request.assert_awaited_once()
    assert "alfred:user:responses" in [c.args[0] for c in redis.xadd.await_args_list]
    redis.xack.assert_awaited_once_with("alfred:user:requests", "conscious-engine", b"1-0")


@pytest.mark.asyncio
async def test_acks_and_skips_an_entry_with_no_event_field() -> None:
    """Unparseable entries must leave the PEL, or they are reclaimed forever."""
    redis, engine = AsyncMock(), _engine()

    await process_request_entry(
        b"1-0",
        {b"junk": b"x"},
        engine=engine,
        redis=redis,
        stream="alfred:user:requests",
        group="conscious-engine",
    )

    engine.process_request.assert_not_awaited()
    redis.xack.assert_awaited_once_with("alfred:user:requests", "conscious-engine", b"1-0")


@pytest.mark.asyncio
async def test_a_failed_request_stays_pending_for_the_next_reclaim() -> None:
    redis, engine = AsyncMock(), _engine()
    engine.process_request.side_effect = RuntimeError("LLM unreachable")

    await process_request_entry(
        b"1-0",
        {b"event": _REQUEST.encode()},
        engine=engine,
        redis=redis,
        stream="alfred:user:requests",
        group="conscious-engine",
    )

    redis.xack.assert_not_awaited()
