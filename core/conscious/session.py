"""SessionManager — conversation state per channel/session."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shared.streams import SESSIONS_KEY_PREFIX

if TYPE_CHECKING:
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages conversation sessions in Redis.

    Each session is a Redis hash at alfred:sessions:{session_id}.
    Sessions expire after configurable idle time.
    """

    def __init__(self, redis: AioRedis, timeout_minutes: int = 30) -> None:
        self._redis = redis
        self._timeout_seconds = timeout_minutes * 60

    def _key(self, session_id: str) -> str:
        return f"{SESSIONS_KEY_PREFIX}{session_id}"

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
        raw: bytes | None = await self._redis.hget(key, "history")  # type: ignore[misc,unused-ignore]
        history: list[dict[str, str]] = json.loads(raw) if raw else []
        history.append({"role": role, "content": content})
        await self._redis.hset(key, "history", json.dumps(history))  # type: ignore[misc,unused-ignore]
        await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc,unused-ignore]

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        """Get the conversation history for a session."""
        key = self._key(session_id)
        raw: bytes | None = await self._redis.hget(key, "history")  # type: ignore[misc,unused-ignore]
        return json.loads(raw) if raw else []
