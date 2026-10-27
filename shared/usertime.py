"""User timezone helpers — single source of truth for the user's local timezone.

Resolution order: stored Redis key -> ALFRED_TIMEZONE env -> UTC.
Single-user by design (one key); per-identity keys are a future extension.
"""

from __future__ import annotations

import json
import logging
import os
from zoneinfo import ZoneInfo

from shared.streams import (
    TRIGGER_SYNC_OP_TZ_CHANGED,
    TRIGGERS_CHANGED_CHANNEL,
    USER_TIMEZONE_KEY,
    decode_stream_value,
)
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


# The zone this process last resolved or stored, for readers that cannot await (the
# spend clock picks the budget day synchronously, on every model request).
_last_resolved: str | None = None


def default_user_timezone() -> str:
    """The configured zone: ALFRED_TIMEZONE when valid, else UTC."""
    env = os.getenv("ALFRED_TIMEZONE", "")
    if env and is_valid_timezone(env):
        return env
    return "UTC"


def last_user_timezone() -> str:
    """The zone ``get_user_timezone`` or ``set_user_timezone`` last saw in this process,
    or the configured default before either has run."""
    return _last_resolved or default_user_timezone()


async def get_user_timezone(redis: AioRedis) -> str:
    """Return the user's IANA timezone name (stored -> env -> UTC)."""
    global _last_resolved
    raw: str | bytes | None = await redis.get(USER_TIMEZONE_KEY)  # type: ignore[misc,unused-ignore]
    name = decode_stream_value(raw) if raw is not None else ""
    _last_resolved = name if is_valid_timezone(name) else default_user_timezone()
    return _last_resolved


async def set_user_timezone(redis: AioRedis, tz_name: str) -> bool:
    """Persist the user's timezone if valid and changed.

    On change, pokes TRIGGERS_CHANGED_CHANNEL so long-sleeping cron alarms
    re-arm under the new zone. Returns True only when a write happened.
    """
    global _last_resolved
    if not is_valid_timezone(tz_name):
        logger.warning("Ignoring invalid client timezone %r", tz_name)
        return False
    _last_resolved = tz_name
    raw: str | bytes | None = await redis.get(USER_TIMEZONE_KEY)  # type: ignore[misc,unused-ignore]
    current = decode_stream_value(raw) if raw is not None else None
    if current == tz_name:
        return False
    await redis.set(USER_TIMEZONE_KEY, tz_name)  # type: ignore[misc,unused-ignore]
    await redis.publish(  # type: ignore[misc,unused-ignore]
        TRIGGERS_CHANGED_CHANNEL, json.dumps({"op": TRIGGER_SYNC_OP_TZ_CHANGED})
    )
    logger.info("User timezone set to %s", tz_name)
    return True
