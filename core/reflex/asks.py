"""Asks: the limits that keep them few, and the asking itself.

``gate`` decides whether a standing proposal may be put to the owner, and says why not when it
may not. ``open_ask`` puts it: it parks the proposed action as a pending action and announces it
with an IMPORTANT notification. ``ask_text`` is the words of that notification. The answers are
``core/reflex/answers.py``'s.

Nothing here names a tool or a home-automation concept: a tool is matched against the owner's
``ask_tools`` patterns, and the words of an ask come from the service half of the tool's name
(passed in by the caller) or, failing that, the end of the name.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any, Final, Literal
from zoneinfo import ZoneInfo

from loguru import logger

from bus.schemas.events import ReflexOutcome
from core.notifications.schema import Urgency
from core.reflex.answers import ASK_SOURCE, OpenAsk, settle_expired
from core.routing.pending import confirmation_metadata, store_pending_action
from shared.streams import REFLEX_ASK_OPEN_KEY, REFLEX_ASKED_PREFIX, REFLEX_ASKS_PREFIX

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from bus.schemas.events import HeldReason, ReflexProposal
    from core.notifications.dnd import DNDChecker
    from core.notifications.publisher import NotificationPublisher
    from core.reflex.settings import ReflexSettings
    from shared.types import AioRedis

# The limits shadow mode checks, in the gate's order: a shadow outcome's reason is one of these.
SHADOW_REASONS: Final = ("tool_off", "quiet_hours", "dnd")
# The most a notification body may be, and the line that always ends it.
BODY_MAX_CHARS: Final = 300
_REPLY_LINE: Final = "Reply yes or no."
_DAY_SECONDS: Final = 86_400
_COUNT_TTL_SECONDS: Final = 2 * _DAY_SECONDS


def asked_key(tool_name: str, targets: Sequence[str]) -> str:
    """The key that says Reflex asked about this tool and these targets lately."""
    return f"{REFLEX_ASKED_PREFIX}{tool_name}:{','.join(sorted(targets))}"


def asks_key(local_day: date) -> str:
    """The key holding the number of asks made on one local day."""
    return f"{REFLEX_ASKS_PREFIX}{local_day.isoformat()}"


def _clock(value: str) -> time:
    """An ``HH:MM`` setting as a time of day."""
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def in_quiet_hours(local: time, start: str, end: str) -> bool:
    """Whether *local* falls in quiet hours, from *start* up to but not including *end*.

    A start later than the end wraps midnight; equal ones mean there are no quiet hours.
    """
    now = local.replace(tzinfo=None)
    begins, ends = _clock(start), _clock(end)
    if begins == ends:
        return False
    if begins < ends:
        return begins <= now < ends
    return now >= begins or now < ends


async def _dnd_on(dnd: DNDChecker) -> bool:
    """Whether do-not-disturb is on; a check that fails counts as on.

    Reflex never asks while the owner may not want to be disturbed, and it cannot tell that
    from an unreadable state (the stored value does not parse, Redis does not answer). Held as
    ``dnd``, the decision is recorded and the event is not retried into the same failure.
    """
    try:
        return (await dnd.is_active()).active
    except Exception as e:
        logger.warning("Reflex could not read do-not-disturb; holding as if it were on: {}", e)
        return True


async def _first_hit(
    redis: AioRedis,
    settings: ReflexSettings,
    tool_name: str,
    targets: Sequence[str],
    local: datetime,
    dnd: DNDChecker,
    *,
    askable: bool,
    cheap_only: bool,
) -> HeldReason | None:
    """The first limit *tool_name* runs into, in the gate's order, or ``None``.

    *cheap_only* stops after the first three: the ones that read nothing of Reflex's own.
    """
    if not askable or not any(fnmatchcase(tool_name, pattern) for pattern in settings.ask_tools):
        return "tool_off"
    if in_quiet_hours(local.time(), settings.quiet_hours_start, settings.quiet_hours_end):
        return "quiet_hours"
    if await _dnd_on(dnd):
        return "dnd"
    if cheap_only:
        return None
    if await redis.exists(REFLEX_ASK_OPEN_KEY):
        return "ask_open"
    if await redis.exists(asked_key(tool_name, targets)):
        return "asked_recently"
    count: bytes | str | None = await redis.get(asks_key(local.date()))
    if (0 if count is None else int(count)) >= settings.daily_ask_cap:
        return "daily_cap"
    return None


async def gate(
    redis: AioRedis,
    settings: ReflexSettings,
    outcome: ReflexOutcome,
    proposal: ReflexProposal,
    *,
    targets: Sequence[str],
    tz_name: str,
    now: datetime,
    dnd: DNDChecker,
    askable: bool = True,
) -> ReflexOutcome:
    """What the ask limits make of a proposal that stands (a ``shadow`` outcome).

    Any other outcome comes back unchanged and settles nothing. For a standing proposal, an
    expired ask is settled first, in either mode, so a lapsed ask ends as ``expired`` whatever
    limit the proposal then meets, and whether or not Reflex is still asking. The limits are
    checked in this order, and the first one hit is the reason: ``tool_off`` (no ``ask_tools``
    pattern matches the tool, or the tool is not *askable*: the router refuses one whose
    declared risk is critical or unknown even when it is confirmed, so a yes could only fail),
    ``quiet_hours`` (the owner's local time, in *tz_name*), ``dnd`` (a check that fails counts
    as on), ``ask_open``, ``asked_recently`` and ``daily_cap`` (counted by local date).

    In ``shadow`` mode only the first three are checked, and the result stays ``shadow``,
    carrying the reason or none. In ``ask`` mode a limit hit is ``held`` with its reason, and
    passing all six is ``asked`` (the runner sets the ``ask_id`` once ``open_ask`` has run).
    The gate writes nothing, apart from the settling of an expired ask.
    """
    action = proposal.action
    if outcome.kind != "shadow" or action is None:
        return outcome
    await settle_expired(redis, now=now)
    local = now.astimezone(ZoneInfo(tz_name))
    shadow = settings.mode == "shadow"
    reason = await _first_hit(
        redis,
        settings,
        action.tool_name,
        targets,
        local,
        dnd,
        askable=askable,
        cheap_only=shadow,
    )
    if shadow:
        return ReflexOutcome(kind="shadow", reason=reason)
    if reason is not None:
        return ReflexOutcome(kind="held", reason=reason)
    return ReflexOutcome(kind="asked")


def _sentence(text: str) -> str:
    """*text* as a sentence: stripped, with a full stop unless it already ends one."""
    text = text.strip()
    if not text or text.endswith((".", "!", "?")):
        return text
    return f"{text}."


def _joined(names: Sequence[str]) -> str:
    """``A``, ``A and B`` or ``A, B and C``."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def ask_text(
    tool_name: str,
    parameters: Mapping[str, Any],  # the words come from the tool and the names, not these
    *,
    noticed: str,
    reason: str,
    names: Sequence[str],
    script: bool,
    service: str | None = None,
) -> tuple[str, str]:
    """The title and the body of an ask.

    The title is ``Shall I {words} {names}?``: *words* are *service* with underscores as spaces
    or, with no service (``None`` or empty), the last dotted segment of *tool_name* the same
    way. For a script it is ``Shall I run the script {names}?``. The body is
    ``{noticed}. {reason} Reply yes or no.``, leaving out an empty reason and never doubling a
    full stop, and at most 300 characters: a longer one has the front shortened with an
    ellipsis so the last line survives whole.
    """
    who = _joined(names)
    if script:
        action = "run the script"
    else:
        words = service or tool_name.rsplit(".", 1)[-1]
        action = words.replace("_", " ")
    title = f"Shall I {action}{f' {who}' if who else ''}?"
    front = " ".join(part for part in (_sentence(noticed), _sentence(reason)) if part)
    body = f"{front} {_REPLY_LINE}" if front else _REPLY_LINE
    if len(body) > BODY_MAX_CHARS:
        room = BODY_MAX_CHARS - len(_REPLY_LINE) - 1  # the space before the line
        body = f"{front[: room - 1].rstrip()}… {_REPLY_LINE}"
    return title, body


async def open_ask(
    redis: AioRedis,
    notifier: NotificationPublisher,
    proposal: ReflexProposal,
    *,
    settings: ReflexSettings,
    origin: Literal["state_change", "trigger_fired"],
    noticed: str,
    targets: Sequence[str],
    names: Mapping[str, str],
    script: bool,
    now: datetime,
    tz_name: str,
    service: str | None = None,
) -> OpenAsk | None:
    """Put *proposal* to the owner, or ``None`` when another ask is already open.

    The open key is claimed first, with ``SET NX``: Reflex's two loops interleave at every
    await, so the gate's read cannot promise one open ask at a time. A lost claim writes
    nothing else. After it, in this order: the action is parked as a pending action for
    ``ask_expiry_seconds``; the repeat key is set for ``repeat_hours`` (none when that is 0);
    the day's count goes up by one; and the notification goes out. A crash after the claim
    leaves the open key behind, so a redelivery finds ``ask_open``. A failure in any step
    propagates: the runner decides what to do with it.

    *names* maps a target to the name the owner knows it by, and *service* is the service half
    of the tool's name, for the words of the ask (see ``ask_text``).
    """
    action = proposal.action
    if action is None:
        raise ValueError("an ask needs a proposal with an action")
    ask = OpenAsk(
        ask_id=action.request_id,
        tool_name=action.tool_name,
        targets=list(targets),
        origin=origin,
        asked_at=now,
        deadline=now + timedelta(seconds=settings.ask_expiry_seconds),
        noticed=noticed,
        script=script,
    )
    claimed = await redis.set(
        REFLEX_ASK_OPEN_KEY,
        ask.model_dump_json(),
        nx=True,
        ex=settings.ask_expiry_seconds + _DAY_SECONDS,
    )
    if not claimed:
        return None
    await store_pending_action(redis, action, ttl_seconds=settings.ask_expiry_seconds)
    if settings.repeat_hours > 0:
        await redis.set(
            asked_key(action.tool_name, targets),
            "1",
            ex=max(1, round(settings.repeat_hours * 3600)),
        )
    day = asks_key(now.astimezone(ZoneInfo(tz_name)).date())
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(day)
        pipe.expire(day, _COUNT_TTL_SECONDS)
        await pipe.execute()
    title, body = ask_text(
        action.tool_name,
        action.parameters,
        noticed=noticed,
        reason=proposal.reason or "",
        names=[names.get(target, target) for target in targets],
        script=script,
        service=service,
    )
    await notifier.publish(
        title,
        body,
        source=ASK_SOURCE,
        urgency=Urgency.IMPORTANT,
        metadata={
            **confirmation_metadata(
                ask.ask_id, action.tool_name, action.parameters, proposal.reason
            ),
            "kind": "reflex_ask",
        },
    )
    return ask
