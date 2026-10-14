"""Turn-failure classification and the dead-letter stream (spec 6b, "Turns")."""

from __future__ import annotations

import fakeredis
import pytest
import redis.exceptions
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UserError

from core.conscious.failures import (
    FAILED_REPLY,
    MAX_DELIVERIES,
    FailureKind,
    classify,
    dead_letter,
)
from shared.redis_streams import forward_range
from shared.streams import DEAD_LETTER_STREAM


def test_failed_reply_and_cap_are_the_spec_values() -> None:
    assert FAILED_REPLY == (
        "I'm afraid I couldn't finish that, sir — my reasoning service isn't answering. "
        "Please try again in a moment."
    )
    assert MAX_DELIVERIES == 3


@pytest.mark.parametrize("status", [400, 401, 402, 404, 422])
def test_a_client_error_status_is_permanent(status: int) -> None:
    assert classify(ModelHTTPError(status, "m")) is FailureKind.PERMANENT


@pytest.mark.parametrize("status", [408, 409, 429, 500, 503, 529])
def test_a_retryable_status_is_transient(status: int) -> None:
    assert classify(ModelHTTPError(status, "m")) is FailureKind.TRANSIENT


@pytest.mark.parametrize(
    "exc",
    [
        ModelAPIError("m", "connection reset"),
        redis.exceptions.ConnectionError("down"),
        redis.exceptions.TimeoutError("slow"),
        TimeoutError(),
        RuntimeError("anything else"),
    ],
    ids=["model-api", "redis-connection", "redis-timeout", "timeout", "unknown"],
)
def test_everything_else_is_transient(exc: BaseException) -> None:
    assert classify(exc) is FailureKind.TRANSIENT


def test_a_usage_mistake_is_permanent() -> None:
    # UserError subclasses RuntimeError, which is otherwise transient.
    assert classify(UserError("bad tool schema")) is FailureKind.PERMANENT


@pytest.mark.asyncio
async def test_dead_letter_records_the_entry_and_why() -> None:
    redis = fakeredis.FakeAsyncRedis()

    await dead_letter(
        redis,
        entry_id="1-0",
        event='{"event_id":"e"}',
        exc=ModelHTTPError(429, "m"),
        deliveries=3,
        reason="exhausted",
    )

    [(_id, fields)] = await forward_range(redis, DEAD_LETTER_STREAM)
    assert fields[b"entry_id"] == b"1-0"
    assert fields[b"event"] == b'{"event_id":"e"}'
    assert fields[b"error_type"] == b"ModelHTTPError"
    assert b"429" in fields[b"error"]
    assert fields[b"deliveries"] == b"3"
    assert fields[b"reason"] == b"exhausted"


@pytest.mark.asyncio
async def test_dead_letter_without_an_exception() -> None:
    redis = fakeredis.FakeAsyncRedis()

    await dead_letter(redis, entry_id="1-0", event="{}", exc=None, deliveries=2, reason="expired")

    [(_id, fields)] = await forward_range(redis, DEAD_LETTER_STREAM)
    assert fields[b"error_type"] == b""
    assert fields[b"reason"] == b"expired"
