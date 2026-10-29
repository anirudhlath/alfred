"""The open ask, and how an ask ends: yes, no, or silence.

``OpenAsk`` is what ``alfred:reflex:ask:open`` holds while an ask waits. It lives here, apart
from ``asks.py``, because ``core/routing/pending.py`` imports this module when an answer
arrives: nothing here may import ``core.routing`` at module scope (that would be a cycle).

An ask ends once. ``record_answer`` writes the answer, whoever gives it: the Door, a reply in a
conversation (both through ``decide``), or ``settle_expired`` for an ask nobody answered. The
key outlives the ask's deadline by a day, so a late settlement still finds it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

from loguru import logger
from pydantic import BaseModel, field_validator

from core.reflex.settings import add_allowed_scripts
from shared.streams import (
    REFLEX_ANSWERED_PREFIX,
    REFLEX_ANSWERS_MAXLEN,
    REFLEX_ANSWERS_STREAM,
    REFLEX_ASK_OPEN_KEY,
)

if TYPE_CHECKING:
    from shared.types import AioRedis

# Every action Reflex proposes carries a source that starts with this. The router refuses such an
# action unless it is benign, and the conscious process sends no "Done" for one that succeeded.
REFLEX_SOURCE_PREFIX: Final = "reflex"

# The source of an action Reflex proposed (and so of the pending action an ask parks).
ASK_SOURCE: Final = f"{REFLEX_SOURCE_PREFIX}-engine"

# The channels a reply can answer an ask on: the ones an IMPORTANT notification reaches. Never
# speech: a "yes" heard in a room has no way to be told apart from the room.
ASK_REPLY_CHANNELS: Final = frozenset({"web_pwa", "ios", "signal"})

# How long the answered marker lives: long past any redelivery of the answer.
_ANSWERED_TTL_SECONDS: Final = 2 * 86_400

# Deletes the key only while it still holds the bytes that were read. An ask can be settled
# and the next one opened between the read and the delete, and an unconditional DEL would take
# the new ask's claim with it.
_DELETE_IF_UNCHANGED: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# Deletes the key only while it still names the ask in ARGV[1]. The value is parsed, never
# compared byte for byte: the writer's serialisation is not ours to reproduce.
_DELETE_IF_NAMES: Final = """
local raw = redis.call('GET', KEYS[1])
if not raw then
  return 0
end
local ok, ask = pcall(cjson.decode, raw)
if ok and type(ask) == 'table' and ask['ask_id'] == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

AnswerKind = Literal["yes", "no", "expired"]


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


class ReflexAnswer(BaseModel):
    """What became of one ask: the entry on ``alfred:reflex:answers``."""

    ask_id: str
    answer: AnswerKind
    time: datetime  # when it was answered; an expiry carries the ask's deadline
    channel: str | None  # where a yes or no came from (``door``, ``web_pwa``, ...); none if expired
    tool_name: str
    targets: list[str]
    origin: Literal["state_change", "trigger_fired"]
    noticed: str


async def _delete_open_if_unchanged(redis: AioRedis, raw: bytes | str) -> None:
    await redis.eval(_DELETE_IF_UNCHANGED, 1, REFLEX_ASK_OPEN_KEY, raw)


async def read_open_ask(redis: AioRedis) -> OpenAsk | None:
    """The open ask, or ``None`` when there is none or the key cannot be read.

    Reads only: an unreadable value is warned about and left for ``settle_expired`` to delete.
    """
    raw: bytes | str | None = await redis.get(REFLEX_ASK_OPEN_KEY)
    if raw is None:
        return None
    try:
        return OpenAsk.model_validate_json(raw)
    except ValueError as e:
        logger.warning("Reflex found an unreadable open ask: {}", e)
        return None


def is_reflex_source(source: str) -> bool:
    """Whether an action's *source* names Reflex (``ActionRequest.source``)."""
    return source.startswith(REFLEX_SOURCE_PREFIX)


async def _allow_scripts(redis: AioRedis, targets: list[str]) -> None:
    """Add *targets* to the owner's ``allowed_scripts``, each once, as best effort.

    The owner said yes to running them, so Reflex may run them from now on. A failure of any
    kind (a Redis error, a stored document that is unusable, a list the addition would push
    past its cap) is warned about and goes no further: the answer is recorded and the action
    runs, and the script is simply not added.
    """
    try:
        _, added = await add_allowed_scripts(redis, [t for t in targets if t.strip()])
    except Exception as e:
        logger.warning("Reflex did not allow the script {}: {}", targets, e)
        return
    if added:
        logger.info("Reflex may now run {}", added)


async def record_answer(
    redis: AioRedis, ask: OpenAsk, answer: AnswerKind, *, at: datetime, channel: str | None
) -> bool:
    """Record how *ask* ended, once; False when it already has an answer.

    ``SET NX`` on the ask's answered marker is the arbiter: of two answers to one ask (a Door
    tap and a reply, a redelivery), only the first goes on. Then, in order: the answer joins
    ``alfred:reflex:answers``; the open key is deleted, but only while it still names this ask
    (the next ask may have taken it); and a ``yes`` to a script adds its targets to the
    owner's ``allowed_scripts``. A crash between the marker and the rest loses the answer, never
    records it twice; ``settle_expired`` still clears the open key at the ask's deadline.
    """
    first = await redis.set(
        f"{REFLEX_ANSWERED_PREFIX}{ask.ask_id}", "1", nx=True, ex=_ANSWERED_TTL_SECONDS
    )
    if not first:
        return False
    entry = ReflexAnswer(
        ask_id=ask.ask_id,
        answer=answer,
        time=at,
        channel=channel,
        tool_name=ask.tool_name,
        targets=ask.targets,
        origin=ask.origin,
        noticed=ask.noticed,
    )
    await redis.xadd(
        REFLEX_ANSWERS_STREAM,
        {"event": entry.model_dump_json()},
        maxlen=REFLEX_ANSWERS_MAXLEN,
        approximate=True,
    )
    await redis.eval(_DELETE_IF_NAMES, 1, REFLEX_ASK_OPEN_KEY, ask.ask_id)
    if answer == "yes" and ask.script:
        await _allow_scripts(redis, ask.targets)
    return True


async def settle_expired(redis: AioRedis, *, now: datetime) -> OpenAsk | None:
    """Settle the open ask when its deadline is at or before *now*; the ask, or ``None``.

    An ask still waiting, or none open, gives ``None`` and changes nothing. A settled one is
    recorded as ``expired``, at its deadline, and its key is deleted whether or not the record
    was new: an ask that already has an answer (a crash between the answer's marker and its
    delete) would otherwise hold every later ask for a day. An open key that cannot be read is
    settled too: it is deleted and warned about, and gives ``None``, so one bad value never
    holds every later ask. Each delete is made only while the key still holds the bytes read.
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
    await record_answer(redis, ask, "expired", at=ask.deadline, channel=None)
    await _delete_open_if_unchanged(redis, raw)
    return ask
