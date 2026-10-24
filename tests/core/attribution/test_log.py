"""Tests for the attribution logs: Alfred's calls, attributed changes, daily cause counts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
from redis import exceptions as redis_exceptions

from bus.schemas.events import ActionRequest, ActionResult, ChangeContext, StateChangedEvent
from core.attribution.log import (
    AlfredCall,
    AttributedChange,
    call_context_id,
    causes_key,
    record_call,
    record_change,
)
from shared.streams import (
    ATTRIBUTION_CALLS_STREAM,
    ATTRIBUTION_CAUSES_PREFIX,
    ATTRIBUTION_CHANGES_STREAM,
    ATTRIBUTION_MAX_AGE,
)

_NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _result(status: str = "success", result: dict[str, Any] | None = None) -> ActionResult:
    return ActionResult(
        source="home-agent",
        request_id="r",
        tool_name="smart_home.dim_lights",
        status="success" if status == "success" else "error",
        result=result,
        error=None if status == "success" else "boom",
    )


def _action() -> ActionRequest:
    return ActionRequest(
        source="reflex-engine",
        request_id="r",
        target_service="home",
        tool_name="smart_home.dim_lights",
        parameters={"entity_id": "light.bedroom_lamp", "brightness": 20},
        reason="bedtime",
    )


def _change(context: ChangeContext | None) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.bedroom_lamp",
        old_state="on",
        new_state="off",
        context=context,
        timestamp=datetime(2026, 10, 24, 11, 59, tzinfo=UTC),
    )


def test_causes_key_is_the_prefix_and_the_utc_date() -> None:
    assert causes_key(date(2026, 10, 24)) == f"{ATTRIBUTION_CAUSES_PREFIX}2026-10-24"
    assert causes_key(date(2026, 10, 24)) == "alfred:attribution:causes:2026-10-24"


def test_call_context_id_reads_the_tool_result() -> None:
    assert call_context_id(_result(result={"id": "r", "result": {"context_id": "abc"}})) == "abc"
    assert call_context_id(_result(result={"id": "r", "result": {}})) is None
    assert call_context_id(_result(result=None)) is None
    assert call_context_id(_result(result={"data": "x"})) is None
    assert call_context_id(_result(result={"result": {"context_id": ""}})) is None
    assert call_context_id(_result(result={"result": {"context_id": 7}})) is None
    assert call_context_id(_result(result={"result": "context_id"})) is None


async def test_record_call_appends_a_successful_call() -> None:
    redis = fakeredis.FakeAsyncRedis()
    result = _result(result={"id": "r", "result": {"context_id": "abc"}})

    await record_call(redis, _action(), result, now=_NOW)

    entries = await redis.xrange(ATTRIBUTION_CALLS_STREAM)
    assert len(entries) == 1
    call = AlfredCall.model_validate_json(entries[0][1][b"call"])
    assert call.time == _NOW
    assert call.source == "reflex-engine"
    assert call.request_id == "r"
    assert call.target_service == "home"
    assert call.tool_name == "smart_home.dim_lights"
    assert call.parameters == {"entity_id": "light.bedroom_lamp", "brightness": 20}
    assert call.reason == "bedtime"
    assert call.context_id == "abc"


async def test_record_call_keeps_a_call_whose_service_returned_no_context_id() -> None:
    redis = fakeredis.FakeAsyncRedis()

    await record_call(redis, _action(), _result(result={"id": "r", "result": {}}), now=_NOW)

    call = AlfredCall.model_validate_json(
        (await redis.xrange(ATTRIBUTION_CALLS_STREAM))[0][1][b"call"]
    )
    assert call.context_id is None


async def test_record_call_skips_an_error_result() -> None:
    redis = fakeredis.FakeAsyncRedis()

    await record_call(redis, _action(), _result("error"), now=_NOW)

    assert await redis.xlen(ATTRIBUTION_CALLS_STREAM) == 0


async def test_record_change_counts_unknown_without_context() -> None:
    redis = fakeredis.FakeAsyncRedis()

    cause = await record_change(redis, _change(None), now=_NOW)

    assert cause is None
    key = causes_key(date(2026, 10, 24))
    assert await redis.hgetall(key) == {b"unknown": b"1"}
    assert await redis.xlen(ATTRIBUTION_CHANGES_STREAM) == 0
    ttl = await redis.ttl(key)
    assert timedelta(days=29).total_seconds() < ttl <= ATTRIBUTION_MAX_AGE.total_seconds()


async def test_record_change_logs_attributed_but_not_outside() -> None:
    redis = fakeredis.FakeAsyncRedis()

    person = await record_change(redis, _change(ChangeContext(id="c1", user_id="u1")), now=_NOW)
    outside = await record_change(redis, _change(ChangeContext(id="c2")), now=_NOW)

    assert person == "person"
    assert outside == "outside"
    assert await redis.hgetall(causes_key(date(2026, 10, 24))) == {
        b"person": b"1",
        b"outside": b"1",
    }
    entries = await redis.xrange(ATTRIBUTION_CHANGES_STREAM)
    assert len(entries) == 1
    change = AttributedChange.model_validate_json(entries[0][1][b"change"])
    assert change.cause == "person"
    assert change.time == datetime(2026, 10, 24, 11, 59, tzinfo=UTC)
    assert change.entity_id == "light.bedroom_lamp"
    assert (change.old_state, change.new_state) == ("on", "off")
    assert change.context == ChangeContext(id="c1", user_id="u1")


async def test_record_change_counts_by_the_utc_day_of_now() -> None:
    redis = fakeredis.FakeAsyncRedis()
    late = datetime(2026, 10, 24, 23, 30, tzinfo=UTC)

    await record_change(redis, _change(ChangeContext(by_service=True)), now=late)
    await record_change(
        redis, _change(ChangeContext(by_service=True)), now=late + timedelta(hours=1)
    )

    assert await redis.hgetall(causes_key(date(2026, 10, 24))) == {b"alfred": b"1"}
    assert await redis.hgetall(causes_key(date(2026, 10, 25))) == {b"alfred": b"1"}


def _refusing_redis() -> MagicMock:
    """A redis at maxmemory: the writes it would need are refused."""
    refusal = redis_exceptions.ResponseError(
        "OOM command not allowed when used memory > 'maxmemory'"
    )
    redis = MagicMock()
    redis.pipeline = MagicMock(side_effect=refusal)
    redis.hincrby = AsyncMock(side_effect=refusal)
    redis.expire = AsyncMock(side_effect=refusal)
    redis.xadd = AsyncMock(side_effect=refusal)
    return redis


async def test_writes_never_raise_when_redis_refuses() -> None:
    redis = _refusing_redis()

    await record_call(redis, _action(), _result(result={"result": {"context_id": "abc"}}), now=_NOW)
    cause = await record_change(redis, _change(ChangeContext(user_id="u1")), now=_NOW)
    unknown = await record_change(redis, _change(None), now=_NOW)

    # The cause is read from the event, not from redis: a refused write changes no decision.
    assert cause == "person"
    assert unknown is None
