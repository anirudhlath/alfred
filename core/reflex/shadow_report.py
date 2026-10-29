"""Shadow report: what Reflex proposed but did not do (#285).

    docker exec alfred python -m core.reflex.shadow_report --days 7

Prints Markdown for the owner's review: daily decision counts and daily outcome counts from
``alfred:reflex:decisions:<UTC date>``, the decisions that would have reversed a person (newest
first, from the decision log), the asks (asked, held by reason, the reasons shadow mode would
have held for, and the answers from ``alfred:reflex:answers``), then every act, ask and invalid
proposal in the window, oldest first, in the user's local time. The proposals name people,
their comings and goings and what was playing, so the report is reviewed privately: only the
counts and verdict tallies go to the public #285 thread. The Asks section is counts alone, with
no target, name or noticed text.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, get_args

from loguru import logger
from pydantic import ValidationError

from bus.schemas.events import HeldReason, OutcomeKind, ReflexObservation, StateChangedEvent
from core.reflex.answers import AnswerKind, ReflexAnswer, settle_expired
from core.reflex.asks import SHADOW_REASONS
from core.reflex.decision_log import DecisionLog
from core.reflex.prompt import render_event
from core.reflex.runner import decisions_key
from shared.config import AlfredConfig
from shared.redis_streams import create_redis, forward_range, revrange
from shared.streams import REFLEX_ANSWERS_STREAM, REFLEX_OBSERVATIONS_STREAM, decode_stream_value
from shared.usertime import get_user_timezone

if TYPE_CHECKING:
    from datetime import date
    from zoneinfo import ZoneInfo

    from core.reflex.decision_log import DecisionRecord
    from shared.types import AioRedis

DECISIONS: tuple[str, ...] = ("act", "ask", "none", "invalid")
# What each decision came to, in the order ``OutcomeKind`` lists them; the daily hash counts
# them as ``outcome:<kind>``.
OUTCOMES: tuple[str, ...] = get_args(OutcomeKind)
# How an ask ends, and why one was held: the columns of the Asks section.
ANSWERS: tuple[str, ...] = get_args(AnswerKind)
HELD_REASONS: tuple[str, ...] = get_args(HeldReason)
# The Asks table's columns, each with the field of the daily hash it reads: asks made, holds by
# reason, then the reasons shadow mode would have held for (the evidence for switching to ask).
ASK_FIELDS: dict[str, str] = {
    "asked": "outcome:asked",
    "held": "outcome:held",
    **{reason: f"held:{reason}" for reason in HELD_REASONS},
    **{f"shadow:{reason}": f"shadow:{reason}" for reason in SHADOW_REASONS},
}
# Upper bound on observations scanned. A week runs to a few thousand; past this the
# report says it was truncated rather than silently dropping the oldest.
MAX_SCAN = 20_000
MAX_RAW_CHARS = 200
# How many would_reverse decisions the report lists; the rest are counted.
MAX_REVERSED = 20
# A day written before outcomes were counted has no ``outcome:*`` field: its cells read as this.
NOT_COUNTED = "—"


def _local(moment: datetime, tz: ZoneInfo) -> str:
    """*moment* in the user's local time, the way every line of the report shows it."""
    return moment.astimezone(tz).strftime("%a %d %b %H:%M")


def _describe(obs: ReflexObservation) -> str:
    """The trigger's name, or the change as Reflex's prompt rendered it (live state aside)."""
    event = obs.trigger_event
    if obs.origin == "trigger_fired":
        return f"trigger {event.get('trigger_name', '?')}"
    try:
        return render_event(StateChangedEvent.model_validate(event), None)
    except ValidationError:
        return str(event.get("entity_id", "?"))


def format_proposal(obs: ReflexObservation, tz: ZoneInfo) -> str:
    """One Markdown bullet for one proposal observation."""
    proposal = obs.proposal
    if proposal is None:
        raise ValueError("format_proposal needs an observation that carries a proposal")
    when = _local(obs.timestamp, tz)
    head = f"- {when} · **{proposal.decision}** · {_describe(obs)}"
    if proposal.decision == "invalid":
        raw = " ".join((proposal.raw or "").split()).replace("`", "'")[:MAX_RAW_CHARS]
        return f"{head} — problem: {proposal.problem} · raw: `{raw}`"
    parts = [head]
    if proposal.reason:
        parts.append(f'"{proposal.reason}"')
    if proposal.action is not None:
        params = ", ".join(f"{k}={v}" for k, v in proposal.action.parameters.items())
        parts.append(f"{proposal.action.tool_name}({params})")
    return " — ".join(parts)


def _counts_table(
    columns: tuple[str, ...],
    days: list[tuple[date, dict[str, str]]],
    *,
    prefix: str,
    fields: dict[str, str] | None = None,
) -> list[str]:
    """A Markdown table: a row per UTC day, a column per counted field.

    A column reads the hash field of its name behind ``prefix``, or the one *fields* names for
    it. With a ``prefix``, a day whose hash holds no field with it at all was written before
    those fields were counted: every cell of that day is ``NOT_COUNTED``, not a zero nobody
    counted. A day with at least one such field counts the kinds it lacks as ``0``.
    """
    lines = [
        f"| Day (UTC) | {' | '.join(columns)} |",
        f"|---|{'---|' * len(columns)}",
    ]
    for day, counts in days:
        counted = not prefix or any(field.startswith(prefix) for field in counts)
        cells = " | ".join(
            counts.get((fields or {}).get(name, f"{prefix}{name}"), "0") if counted else NOT_COUNTED
            for name in columns
        )
        lines.append(f"| {day.isoformat()} | {cells} |")
    return lines


def format_reversal(record: DecisionRecord, tz: ZoneInfo) -> str:
    """One Markdown bullet for one decision that would have reversed a person."""
    action = record.proposal.action
    tool = "?" if action is None else action.tool_name
    target = "?" if action is None else action.parameters.get("target", "?")
    reversed_ids = ", ".join(record.outcome.reversed) if record.outcome else "?"
    return f"- {_local(record.time, tz)} · {tool} · target {target} · reversed {reversed_ids}"


async def _reversal_lines(
    redis: AioRedis, *, start: datetime, now: datetime, tz: ZoneInfo
) -> list[str]:
    """The ``Would have reversed`` section's lines, read from the decision log over the window.

    Newest first, at most ``MAX_REVERSED``. A log that cannot be read costs this section only.
    """
    try:
        records = await DecisionLog(redis).read(since=start, until=now)
    except Exception as e:
        logger.warning("Shadow report could not read the decision log: {}", e)
        return ["The decision log could not be read, so this list is unavailable."]
    reversing = [r for r in reversed(records) if r.outcome and r.outcome.kind == "would_reverse"]
    if not reversing:
        return ["None."]
    lines = [format_reversal(record, tz) for record in reversing[:MAX_REVERSED]]
    if len(reversing) > MAX_REVERSED:
        lines.append(f"({len(reversing) - MAX_REVERSED} more)")
    return lines


async def _answers_by_day(redis: AioRedis, *, start: datetime) -> dict[date, Counter[str]]:
    """How many asks ended yes, no and expired on each UTC day, over the window.

    A day is the one the answer was recorded on (its stream entry's time): an ask nobody
    answered is recorded when it is settled, which is when something next looked. An entry
    that cannot be read is skipped.
    """
    tallies: dict[date, Counter[str]] = {}
    for entry_id, fields in await forward_range(
        redis, REFLEX_ANSWERS_STREAM, min_id=str(int(start.timestamp() * 1000))
    ):
        raw = fields.get(b"event") or fields.get("event")
        if raw is None:
            continue
        try:
            answer = ReflexAnswer.model_validate_json(decode_stream_value(raw))
        except ValueError:
            continue
        recorded = int(decode_stream_value(entry_id).split("-", 1)[0]) / 1000
        day = datetime.fromtimestamp(recorded, UTC).date()
        tallies.setdefault(day, Counter())[answer.answer] += 1
    return tallies


async def _ask_lines(
    redis: AioRedis, *, days: list[tuple[date, dict[str, str]]], start: datetime, now: datetime
) -> list[str]:
    """The ``Asks`` section's lines: counts only, never a target, a name or what was noticed.

    An ask whose deadline has passed is settled first, so a lapsed one counts as expired (a
    settle that fails is warned about and costs nothing else). The answers come from
    ``alfred:reflex:answers``; a stream that cannot be read costs the answers table only.
    """
    try:
        await settle_expired(redis, now=now)
    except Exception as e:
        logger.warning("Shadow report could not settle the open ask: {}", e)
    lines = [
        "_Counts only. `held` is a proposal a limit stopped, under the limit that stopped it."
        " `shadow:<reason>` is the same limit met while Reflex was in shadow mode, where nothing"
        " is asked. An ask nobody answered counts as `expired` once it is settled, which this"
        " report does first._",
        "",
        *_counts_table(tuple(ASK_FIELDS), days, prefix="outcome:", fields=ASK_FIELDS),
        "",
    ]
    try:
        tallies = await _answers_by_day(redis, start=start)
    except Exception as e:
        logger.warning("Shadow report could not read the answers: {}", e)
        return [*lines, "The answers could not be read, so their table is unavailable."]
    lines += [f"| Day (UTC) | {' | '.join(ANSWERS)} |", f"|---|{'---|' * len(ANSWERS)}"]
    for day, _ in days:
        cells = " | ".join(str(tallies.get(day, Counter())[answer]) for answer in ANSWERS)
        lines.append(f"| {day.isoformat()} | {cells} |")
    return lines


async def build_report(redis: AioRedis, *, days: int, now: datetime, tz_name: str) -> str:
    """Markdown: per-day decision and outcome counts, the decisions that would have reversed a
    person, the asks and their answers, then every non-none proposal in the window."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    start = now - timedelta(days=days)
    days_counted: list[tuple[date, dict[str, str]]] = []
    day = start.astimezone(UTC).date()
    while day <= now.astimezone(UTC).date():
        raw_counts = await redis.hgetall(decisions_key(day))
        counts = {decode_stream_value(k): decode_stream_value(v) for k, v in raw_counts.items()}
        days_counted.append((day, counts))
        day += timedelta(days=1)

    lines = [
        f"## Reflex shadow report: the last {days} days (times in {tz_name})",
        "",
        *_counts_table(DECISIONS, days_counted, prefix=""),
        "",
        "### Outcomes",
        "",
        "_What each decision came to. `would_reverse` is a proposal that would have undone what"
        " a person just changed: counted, never published._",
        "",
        *_counts_table(OUTCOMES, days_counted, prefix="outcome:"),
        "",
        "### Would have reversed",
        "",
        *await _reversal_lines(redis, start=start, now=now, tz=tz),
        "",
        "### Asks",
        "",
        *await _ask_lines(redis, days=days_counted, start=start, now=now),
    ]

    entries = await revrange(
        redis,
        REFLEX_OBSERVATIONS_STREAM,
        count=MAX_SCAN,
        min_id=f"{int(start.timestamp() * 1000)}-0",
    )
    proposals: list[ReflexObservation] = []
    for _entry_id, fields in reversed(entries):  # oldest first
        raw_event = fields.get(b"event") or fields.get("event")
        if raw_event is None:
            continue
        try:
            obs = ReflexObservation.model_validate_json(decode_stream_value(raw_event))
        except ValueError:
            continue
        if obs.proposal is not None and obs.proposal.decision != "none":
            proposals.append(obs)

    lines += ["", f"### Proposals ({len(proposals)})", ""]
    lines += [format_proposal(obs, tz) for obs in proposals] or ["None in this window."]
    if len(entries) >= MAX_SCAN:
        lines += [
            "",
            f"_Scanned the newest {MAX_SCAN} observations only; older ones in the window"
            " are not shown._",
        ]
    return "\n".join(lines)


async def _main(days: int) -> None:
    config = AlfredConfig.from_env()
    redis = create_redis(config.redis_url)
    try:
        tz_name = await get_user_timezone(redis)
        print(await build_report(redis, days=days, now=datetime.now(UTC), tz_name=tz_name))
    finally:
        await redis.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reflex shadow report (#285)")
    parser.add_argument("--days", type=int, default=7, help="window length (default 7)")
    args = parser.parse_args()
    asyncio.run(_main(args.days))


if __name__ == "__main__":
    main()
