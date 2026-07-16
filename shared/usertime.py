"""User timezone helpers — single source of truth for the user's local timezone.

Resolution order: stored Redis key -> ALFRED_TIMEZONE env -> UTC.
Single-user by design (one key); per-identity keys are a future extension.
"""

from __future__ import annotations

import json
import logging
import os
from zoneinfo import ZoneInfo

from shared.streams import TRIGGERS_CHANGED_CHANNEL, USER_TIMEZONE_KEY
from shared.types import AioRedis  # noqa: TC001

logger = logging.getLogger(__name__)


def is_valid_timezone(tz_name: str) -> bool:
    """Return True if *tz_name* is a resolvable IANA timezone name."""
    if not tz_name:
        return False
    try:
        ZoneInfo(tz_name)
    except Exception:
        return False
    return True


async def get_user_timezone(redis: AioRedis) -> str:
    """Return the user's IANA timezone name (stored -> env -> UTC)."""
    raw: str | bytes | None = await redis.get(USER_TIMEZONE_KEY)  # type: ignore[misc,unused-ignore]
    if raw is not None:
        name = raw.decode() if isinstance(raw, bytes) else raw
        if is_valid_timezone(name):
            return name
    env = os.getenv("ALFRED_TIMEZONE", "")
    if env and is_valid_timezone(env):
        return env
    return "UTC"


async def set_user_timezone(redis: AioRedis, tz_name: str) -> bool:
    """Persist the user's timezone if valid and changed.

    On change, pokes TRIGGERS_CHANGED_CHANNEL so long-sleeping cron alarms
    re-arm under the new zone. Returns True only when a write happened.
    """
    if not is_valid_timezone(tz_name):
        logger.warning("Ignoring invalid client timezone %r", tz_name)
        return False
    raw: str | bytes | None = await redis.get(USER_TIMEZONE_KEY)  # type: ignore[misc,unused-ignore]
    current = raw.decode() if isinstance(raw, bytes) else raw
    if current == tz_name:
        return False
    await redis.set(USER_TIMEZONE_KEY, tz_name)  # type: ignore[misc,unused-ignore]
    await redis.publish(TRIGGERS_CHANGED_CHANNEL, json.dumps({"op": "tz-changed"}))  # type: ignore[misc,unused-ignore]
    logger.info("User timezone set to %s", tz_name)
    return True
