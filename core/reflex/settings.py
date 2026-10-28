"""Reflex's settings: one JSON document in Redis, read on every decision.

A missing, unreadable or invalid document reads as the defaults, so a typo in a setting can
never stop Reflex. Each distinct bad value is warned about once for the life of the process:
the document is read for every decision, and the same warning on each would bury the log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.streams import REFLEX_SETTINGS_KEY

if TYPE_CHECKING:
    from shared.types import AioRedis

# The bad documents already warned about (raw, as read), so a repeat read is silent.
_warned: set[bytes | str] = set()


class ReflexSettings(BaseModel):
    """What the owner can tune about Reflex. Unknown fields are refused, not ignored."""

    model_config = ConfigDict(extra="forbid")

    # How long after a person changes something Reflex will not propose moving it back.
    reverse_window_minutes: int = Field(60, ge=1, le=1440)


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or 'document'}: {err['msg']}"
        for err in error.errors()
    )


async def load_settings(redis: AioRedis) -> ReflexSettings:
    """The stored settings, or the defaults when there are none or they cannot be used.

    A Redis error is not caught here: it is not a bad document.
    """
    raw: bytes | str | None = await redis.get(REFLEX_SETTINGS_KEY)
    if raw is None:
        return ReflexSettings()
    try:
        return ReflexSettings.model_validate_json(raw)
    except ValidationError as e:
        if raw not in _warned:
            _warned.add(raw)
            logger.warning("Reflex settings unusable, using the defaults: {}", _problems(e))
        return ReflexSettings()


async def save_settings(redis: AioRedis, settings: ReflexSettings) -> None:
    """Store *settings* as the document ``load_settings`` reads."""
    await redis.set(REFLEX_SETTINGS_KEY, settings.model_dump_json())
