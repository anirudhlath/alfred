"""Tests for SessionManager: full message history per session (spec, component 5)."""

from __future__ import annotations

import json
import logging

import fakeredis
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from core.conscious.session import (
    IDENTITY_FIELD,
    LEGACY_HISTORY_FIELD,
    MESSAGES_FIELD,
    PENDING_APPROVAL_FIELD,
    SessionManager,
    legacy_messages,
    stored_messages,
    text_turn,
    turn_count,
)
from shared.streams import SESSIONS_KEY_PREFIX

KEY = f"{SESSIONS_KEY_PREFIX}sess-1"


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


def _texts(messages: list[ModelMessage]) -> list[tuple[str, str]]:
    """Each message as (kind, its first part's text), for comparing without timestamps."""
    out: list[tuple[str, str]] = []
    for m in messages:
        part = m.parts[0]
        content = getattr(part, "content", None)
        out.append((m.kind, content if isinstance(content, str) else type(part).__name__))
    return out


def _tool_turn() -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart("Is the desk lamp on?")], instructions="BIG PROMPT"),
        ModelResponse(parts=[ToolCallPart("home_get_lights", {"entity_id": "light.desk"}, "c1")]),
        ModelRequest(
            parts=[ToolReturnPart("home_get_lights", {"state": "on"}, "c1")],
            instructions="BIG PROMPT",
        ),
        ModelResponse(parts=[TextPart("It is on, sir.")]),
    ]


async def test_a_new_session_is_created_empty_with_its_channel_and_ttl(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    session = await SessionManager(redis, timeout_minutes=45).load("sess-1", "web_pwa", "sir")

    assert (session.channel, session.messages) == ("web_pwa", [])
    assert await redis.hget(KEY, "channel") == b"web_pwa"
    assert await redis.hget(KEY, "created_at")
    assert 0 < await redis.ttl(KEY) <= 45 * 60


async def test_a_stored_run_is_read_back_with_its_tool_calls(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis)
    await mgr.load("sess-1", "web_pwa", "sir")

    await mgr.store_run("sess-1", _tool_turn(), "sir")
    session = await mgr.load("sess-1", "web_pwa", "sir")

    assert _texts(session.messages) == [
        ("request", "Is the desk lamp on?"),
        ("response", "ToolCallPart"),
        ("request", "ToolReturnPart"),
        ("response", "It is on, sir."),
    ]
    tool_return = session.messages[2].parts[0]
    assert isinstance(tool_return, ToolReturnPart)
    assert tool_return.content == {"state": "on"}


async def test_stored_requests_carry_no_instructions(redis: fakeredis.FakeAsyncRedis) -> None:
    mgr = SessionManager(redis)

    await mgr.store_run("sess-1", _tool_turn(), "sir")

    raw = await redis.hget(KEY, MESSAGES_FIELD)
    assert raw is not None and b"BIG PROMPT" not in raw
    messages = ModelMessagesTypeAdapter.validate_json(raw)
    assert all(m.instructions is None for m in messages if isinstance(m, ModelRequest))


async def test_store_run_replaces_the_history_and_records_the_speaker(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis, timeout_minutes=30)
    await mgr.store_run("sess-1", _tool_turn(), "sir")

    await mgr.store_run("sess-1", text_turn("Hello", "Good evening, sir."), "sir")

    session = await mgr.load("sess-1", "web_pwa", "sir")
    assert _texts(session.messages) == [("request", "Hello"), ("response", "Good evening, sir.")]
    assert await redis.hget(KEY, IDENTITY_FIELD) == b"sir"
    assert 0 < await redis.ttl(KEY) <= 30 * 60


async def test_another_speakers_history_is_not_read(redis: fakeredis.FakeAsyncRedis) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sat-kitchen", text_turn("Unlock the door", "Unlocked, sir."), "sir")

    session = await mgr.load("sat-kitchen", "satellite", "guest")

    assert session.messages == []


async def test_store_run_with_a_pending_approval_round_trips(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis)

    await mgr.store_run("sess-1", _tool_turn(), "sir", pending_approval="appr-1")

    session = await mgr.load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == "appr-1"
    assert await redis.hget(KEY, PENDING_APPROVAL_FIELD) == b"appr-1"
    assert len(session.messages) == 4


async def test_store_run_without_one_clears_it(redis: fakeredis.FakeAsyncRedis) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sess-1", _tool_turn(), "sir", pending_approval="appr-1")

    await mgr.store_run("sess-1", text_turn("Hello", "Good evening, sir."), "sir")

    assert (await mgr.load("sess-1", "web_pwa", "sir")).pending_approval is None
    assert await redis.hget(KEY, PENDING_APPROVAL_FIELD) is None


async def test_another_speaker_never_sees_the_pending_approval(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sat-kitchen", _tool_turn(), "sir", pending_approval="appr-1")

    session = await mgr.load("sat-kitchen", "satellite", "guest")

    assert session.pending_approval is None
    assert session.messages == []


async def test_append_text_turn_keeps_the_pending_approval(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sess-1", _tool_turn(), "sir", pending_approval="appr-1")

    await mgr.append_text_turn("sess-1", "sir", "Unlock the door", "I've asked, sir.")

    session = await mgr.load("sess-1", "web_pwa", "sir")
    assert session.pending_approval == "appr-1"
    assert len(session.messages) == 6


async def test_append_text_turn_by_another_speaker_drops_the_pending_approval(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sat-kitchen", _tool_turn(), "sir", pending_approval="appr-1")

    await mgr.append_text_turn("sat-kitchen", "guest", "Lights on", "Done.")

    assert await redis.hget(f"{SESSIONS_KEY_PREFIX}sat-kitchen", PENDING_APPROVAL_FIELD) is None


async def test_a_legacy_text_history_is_converted_and_dropped_on_the_next_write(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    history = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello, sir."}]
    await redis.hset(KEY, mapping={"channel": "signal", LEGACY_HISTORY_FIELD: json.dumps(history)})
    mgr = SessionManager(redis)

    session = await mgr.load("sess-1", "signal", "guest")
    assert _texts(session.messages) == [("request", "Hi"), ("response", "Hello, sir.")]

    await mgr.store_run("sess-1", session.messages, "guest")
    assert await redis.hget(KEY, LEGACY_HISTORY_FIELD) is None
    assert await redis.hget(KEY, "channel") == b"signal"


async def test_messages_win_over_a_legacy_history_and_the_next_write_drops_it(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """The rollback-then-forward case: a hash holding both fields reads from ``messages``."""
    legacy = [{"role": "user", "content": "Old"}, {"role": "assistant", "content": "Stale."}]
    stored = ModelMessagesTypeAdapter.dump_json(text_turn("New", "Fresh, sir.")).decode()
    await redis.hset(
        KEY,
        mapping={
            "channel": "web_pwa",
            MESSAGES_FIELD: stored,
            LEGACY_HISTORY_FIELD: json.dumps(legacy),
        },
    )
    mgr = SessionManager(redis)

    session = await mgr.load("sess-1", "web_pwa", "sir")
    assert _texts(session.messages) == [("request", "New"), ("response", "Fresh, sir.")]

    await mgr.store_run("sess-1", session.messages, "sir")
    assert await redis.hget(KEY, LEGACY_HISTORY_FIELD) is None


async def test_unreadable_messages_read_as_no_history(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    await redis.hset(KEY, mapping={"channel": "web_pwa", MESSAGES_FIELD: '[{"kind": "nope"}]'})

    with caplog.at_level(logging.WARNING, logger="core.conscious.session"):
        session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")

    assert session.messages == []
    assert "Unreadable session history" in caplog.text


async def test_append_text_turn_adds_to_the_stored_run(redis: fakeredis.FakeAsyncRedis) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sess-1", _tool_turn(), "sir")

    await mgr.append_text_turn("sess-1", "sir", "Turn on the porch light", "I did, sir.")

    session = await mgr.load("sess-1", "web_pwa", "sir")
    assert _texts(session.messages)[-2:] == [
        ("request", "Turn on the porch light"),
        ("response", "I did, sir."),
    ]
    assert len(session.messages) == 6


async def test_append_text_turn_by_another_speaker_starts_over(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sat-kitchen", _tool_turn(), "sir")

    await mgr.append_text_turn("sat-kitchen", "guest", "Lights on", "Done.")

    raw = await redis.hgetall(f"{SESSIONS_KEY_PREFIX}sat-kitchen")
    assert _texts(stored_messages(raw)) == [("request", "Lights on"), ("response", "Done.")]
    assert raw[IDENTITY_FIELD.encode()] == b"guest"


async def test_append_text_turn_on_a_missing_session_creates_it(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await SessionManager(redis).append_text_turn("sess-1", "sir", "Hi", "Hello, sir.")

    assert _texts(stored_messages(await redis.hgetall(KEY))) == [
        ("request", "Hi"),
        ("response", "Hello, sir."),
    ]


def test_legacy_messages_keep_user_and_assistant_text_only() -> None:
    messages = legacy_messages(
        [
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "ignored"},
            {"role": "assistant", "content": "Good evening, sir."},
        ]
    )

    assert _texts(messages) == [("request", "Hi"), ("response", "Good evening, sir.")]


def test_turn_count_counts_user_prompts_not_tool_returns() -> None:
    assert turn_count(_tool_turn() + text_turn("And now?", "Still on, sir.")) == 2
    assert turn_count([]) == 0


def test_stored_messages_reads_str_or_bytes_fields() -> None:
    payload = ModelMessagesTypeAdapter.dump_json(text_turn("Hi", "Hello")).decode()

    assert len(stored_messages({MESSAGES_FIELD: payload})) == 2
    assert len(stored_messages({MESSAGES_FIELD.encode(): payload.encode()})) == 2
    assert stored_messages({}) == []
