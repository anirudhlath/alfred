"""SessionManager — conversation state per session (spec, component 5).

A session is the Redis hash ``alfred:sessions:{id}``: ``channel``, ``created_at``,
``identity`` (who wrote it last) and ``messages``, the last run's whole message history,
tool calls and results included, as ``ModelMessagesTypeAdapter`` JSON. Each turn replaces
``messages``: compaction rewrites older messages during the run, so the run's history is
the session. Sessions expire after the configured idle time.

A turn by a different identity than the one that wrote the session reads no history, so a
shared session (a satellite's ``sat-{name}``) starts over when the speaker changes. A
session written before full history holds a ``history`` field of user and assistant text
instead; it is read as text turns and deleted on the next write.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from shared.streams import SESSIONS_KEY_PREFIX

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shared.types import AioRedis

logger = logging.getLogger(__name__)

MESSAGES_FIELD: Final = "messages"
IDENTITY_FIELD: Final = "identity"
LEGACY_HISTORY_FIELD: Final = "history"


@dataclass(frozen=True)
class Session:
    """A session as a turn reads it: *messages* is the run's message history."""

    channel: str
    messages: list[ModelMessage]


def text_turn(user: str, reply: str) -> list[ModelMessage]:
    """One turn as text only: the user's words and the reply they were sent."""
    return [ModelRequest(parts=[UserPromptPart(user)]), ModelResponse(parts=[TextPart(reply)])]


def legacy_messages(history: list[dict[str, str]]) -> list[ModelMessage]:
    """A pre-full-history session's text turns as messages. Anything else is skipped."""
    messages: list[ModelMessage] = []
    for turn in history:
        content = turn.get("content", "")
        if turn.get("role") == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content)]))
        elif turn.get("role") == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content)]))
    return messages


def _decoded(raw: Mapping[Any, bytes | str]) -> dict[str, str]:
    return {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }


def stored_messages(raw: Mapping[Any, bytes | str]) -> list[ModelMessage]:
    """A session hash's messages: ``messages`` when present, else the legacy ``history``
    converted. Unreadable JSON is logged and read as no history, so one bad session never
    fails every turn on it."""
    fields = _decoded(raw)
    try:
        if MESSAGES_FIELD in fields:
            return ModelMessagesTypeAdapter.validate_json(fields[MESSAGES_FIELD])
        return legacy_messages(json.loads(fields.get(LEGACY_HISTORY_FIELD) or "[]"))
    except (ValueError, AttributeError, TypeError):
        # ValueError covers pydantic's ValidationError and JSONDecodeError; the other two
        # are a legacy history that is not a list of objects.
        logger.warning("Unreadable session history; reading it as empty", exc_info=True)
        return []


def turn_count(messages: Sequence[ModelMessage]) -> int:
    """User turns in *messages*: requests that carry the user's words."""
    return sum(
        1
        for m in messages
        if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)
    )


def _without_instructions(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """*messages* with every request's instructions dropped: each run sends its own."""
    return [
        replace(m, instructions=None) if isinstance(m, ModelRequest) and m.instructions else m
        for m in messages
    ]


class SessionManager:
    """Manages conversation sessions in Redis (see the module docstring)."""

    def __init__(self, redis: AioRedis, timeout_minutes: int = 30) -> None:
        self._redis = redis
        self._timeout_seconds = timeout_minutes * 60

    def _key(self, session_id: str) -> str:
        return f"{SESSIONS_KEY_PREFIX}{session_id}"

    async def _fields(self, key: str) -> dict[str, str]:
        raw: dict[bytes | str, bytes | str] = await self._redis.hgetall(key)  # type: ignore[misc,unused-ignore]
        return _decoded(raw)

    @staticmethod
    def _history_for(fields: dict[str, str], identity: str) -> list[ModelMessage]:
        """The stored history *identity* may continue: none when someone else wrote it."""
        writer = fields.get(IDENTITY_FIELD)
        if writer is not None and writer != identity:
            logger.info("Session speaker changed; starting without its history")
            return []
        return stored_messages(fields)

    async def load(self, session_id: str, channel: str, identity: str) -> Session:
        """The session a turn by *identity* continues, created when new. Refreshes the TTL."""
        key = self._key(session_id)
        fields = await self._fields(key)
        if fields:
            session = Session(
                channel=fields.get("channel", channel),
                messages=self._history_for(fields, identity),
            )
        else:
            session = Session(channel=channel, messages=[])
            await self._redis.hset(  # type: ignore[misc,unused-ignore]
                key,
                mapping={"channel": channel, "created_at": datetime.now(UTC).isoformat()},
            )
        await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc,unused-ignore]
        return session

    async def store_run(
        self, session_id: str, messages: Sequence[ModelMessage], identity: str
    ) -> None:
        """Replace the session's history with a finished turn's *messages*, written by
        *identity*. Instructions are dropped first; the legacy text field is deleted."""
        key = self._key(session_id)
        payload = ModelMessagesTypeAdapter.dump_json(_without_instructions(messages)).decode()
        await self._redis.hset(  # type: ignore[misc,unused-ignore]
            key, mapping={MESSAGES_FIELD: payload, IDENTITY_FIELD: identity}
        )
        await self._redis.hdel(key, LEGACY_HISTORY_FIELD)  # type: ignore[misc,unused-ignore]
        await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc,unused-ignore]

    async def append_text_turn(self, session_id: str, identity: str, user: str, reply: str) -> None:
        """Add a turn known only as text (one answered from its side-effect ledger) to the
        history *identity* may continue, and store the result."""
        fields = await self._fields(self._key(session_id))
        history = self._history_for(fields, identity)
        await self.store_run(session_id, [*history, *text_turn(user, reply)], identity)

    # ── text-only API, removed in Task 2 ────────────────────────────────────────────

    async def get_or_create(self, session_id: str, channel: str) -> dict[str, Any]:
        """Get an existing session or create a new one."""
        key = self._key(session_id)
        raw: dict[bytes | str, bytes | str] = await self._redis.hgetall(key)  # type: ignore[misc,unused-ignore]

        if raw:
            history_raw = raw.get(b"history") or raw.get("history") or b"[]"
            h = history_raw.decode() if isinstance(history_raw, bytes) else history_raw
            ch = raw.get(b"channel") or raw.get("channel") or channel
            ch_str = ch.decode() if isinstance(ch, bytes) else ch
            session: dict[str, Any] = {
                "channel": ch_str,
                "history": json.loads(h),
            }
        else:
            session = {
                "channel": channel,
                "history": [],
            }
            await self._redis.hset(  # type: ignore[misc,unused-ignore]
                key,
                mapping={
                    "channel": channel,
                    "history": "[]",
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc,unused-ignore]

        # Refresh TTL on access
        await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc,unused-ignore]
        return session

    async def append_turn(self, session_id: str, role: str, content: str) -> None:
        """Append a conversation turn to the session history."""
        key = self._key(session_id)
        raw: bytes | str | None = await self._redis.hget(key, "history")
        history: list[dict[str, str]] = json.loads(raw) if raw else []
        history.append({"role": role, "content": content})
        await self._redis.hset(key, "history", json.dumps(history))  # type: ignore[misc,unused-ignore]
        await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc,unused-ignore]

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        """Get the conversation history for a session."""
        key = self._key(session_id)
        raw: bytes | str | None = await self._redis.hget(key, "history")
        return json.loads(raw) if raw else []
