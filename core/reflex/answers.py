"""The open ask: the one question Reflex has put to the owner and not yet seen answered.

``OpenAsk`` is what ``alfred:reflex:ask:open`` holds while an ask waits. It lives here, apart
from ``asks.py``, because ``core/routing/pending.py`` imports this module when an answer
arrives: nothing here may import ``core.routing`` at module scope (that would be a cycle).

``settle_expired`` is how an ask that nobody answered ends. The key outlives the ask's deadline
by a day, so a late settlement still finds it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

from loguru import logger
from pydantic import BaseModel, field_validator

from shared.streams import REFLEX_ASK_OPEN_KEY

if TYPE_CHECKING:
    from shared.types import AioRedis

# Deletes the key only while it still holds the bytes that were read. An ask can be settled
# and the next one opened between the read and the delete, and an unconditional DEL would take
# the new ask's claim with it.
_DELETE_IF_UNCHANGED: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class OpenAsk(BaseModel):
    """One ask that is waiting for the owner."""

    ask_id: str  # the proposed action's request_id, which is also its pending record's id
    tool_name: str
    targets: list[str]
    origin: Literal["state_change", "trigger_fired"]
    asked_at: datetime  # always aware: a stored time with no offset is read as UTC
    deadline: datetime  # asked_at + ask_expiry_seconds, aware in the same way
    noticed: str  # the event line, or the trigger's name
    script: bool  # the tool's risk is ``elevated``: the ask runs a script

    @field_validator("asked_at", "deadline")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _delete_open_if_unchanged(redis: AioRedis, raw: bytes | str) -> None:
    await redis.eval(_DELETE_IF_UNCHANGED, 1, REFLEX_ASK_OPEN_KEY, raw)


async def settle_expired(redis: AioRedis, *, now: datetime) -> OpenAsk | None:
    """Settle the open ask when its deadline is at or before *now*; the ask, or ``None``.

    An ask still waiting, or none open, gives ``None`` and changes nothing. An open key that
    cannot be read is settled too: it is deleted and warned about, and gives ``None``, so one
    bad value never holds every later ask. The delete is made only while the key still holds
    the bytes read.
    """
    raw: bytes | str | None = await redis.get(REFLEX_ASK_OPEN_KEY)
    if raw is None:
        return None
    try:
        ask = OpenAsk.model_validate_json(raw)
    except ValueError as e:
        logger.warning("Reflex deleted an unreadable open ask: {}", e)
        await _delete_open_if_unchanged(redis, raw)
        return None
    if ask.deadline > now:
        return None
    # Task 12 replaces this delete with
    # ``record_answer(redis, ask, "expired", at=ask.deadline, channel=None)``.
    await _delete_open_if_unchanged(redis, raw)
    return ask
