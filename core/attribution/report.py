"""Attribution report: who changed the house, and what Alfred changed and why.

    docker exec alfred python -m core.attribution.report --days 7

Prints Markdown for the owner's review, oldest first, in the user's local time: the daily
count of changes by cause (``alfred:attribution:causes:<UTC date>``), every call Alfred made
with the changes it caused, then every other change a person or an automation made. The
output names people and devices, so it is read privately.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from loguru import logger
from pydantic import BaseModel

from core.attribution.cause import cause_words
from core.attribution.log import AlfredCall, AttributedChange, causes_key
from shared.config import AlfredConfig
from shared.redis_streams import create_redis, forward_range
from shared.streams import (
    ATTRIBUTION_CALLS_STREAM,
    ATTRIBUTION_CHANGES_STREAM,
    decode_stream_value,
)
from shared.usertime import get_user_timezone

if TYPE_CHECKING:
    from collections.abc import Mapping
    from zoneinfo import ZoneInfo

    from shared.types import AioRedis

# The part of Alfred that asked for a call (``ActionRequest.source``), as the report names it.
PARTS: Final[Mapping[str, str]] = {
    "reflex-engine": "Reflex",
    "conscious-engine": "System 2",
    "trigger-engine": "a trigger",
    "admin-api": "the admin API",
}
CAUSE_COLUMNS: Final = ("person", "alfred", "automation", "outside", "unknown")
_NO_OLD_STATE: Final = "(none)"  # the same word Reflex's event line uses for a first report
_NONE_IN_WINDOW: Final = "None in this window."
_MISMATCH: Final = (
    "{n} changes matched Alfred's calls but were not attributed to Alfred: "
    "home-service is not connected to Home Assistant as a user of its own."
)


def _when(moment: datetime, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).strftime("%a %d %b %H:%M")


async def _read[R: BaseModel](
    redis: AioRedis, stream: str, field: str, model: type[R], start: datetime
) -> list[R]:
    """The records on *stream* written since *start*, oldest first; undecodable ones skipped.

    The stream ids are write times, so the scan starts at *start*'s millisecond id; a record's
    own time is then checked by the caller. A stream is capped (``*_MAXLEN``), so the scan is
    bounded.
    """
    entries = await forward_range(
        redis, stream, min_id=f"{int(start.astimezone(UTC).timestamp() * 1000)}-0"
    )
    records: list[R] = []
    for entry_id, fields in entries:
        raw = fields.get(field.encode()) or fields.get(field)
        if raw is None:
            logger.debug("Attribution report: {} entry {} has no {!r}", stream, entry_id, field)
            continue
        try:
            records.append(model.model_validate_json(decode_stream_value(raw)))
        except ValueError:
            logger.debug("Attribution report: skipped an invalid {} entry {}", stream, entry_id)
    return records


def _change_text(change: AttributedChange, tz: ZoneInfo) -> str:
    """``time · entity: old → new · by whom`` for one change."""
    old = _NO_OLD_STATE if change.old_state is None else change.old_state
    text = f"{_when(change.time, tz)} · {change.entity_id}: {old} → {change.new_state}"
    words = cause_words(change.context)
    return f"{text} · {words}" if words else text


def _call_text(call: AlfredCall, tz: ZoneInfo) -> str:
    """``time · part · tool(params)`` for one call, then the reason in quotes when it gave one."""
    params = ", ".join(f"{k}={v}" for k, v in call.parameters.items())
    part = PARTS.get(call.source, call.source)
    text = f"{_when(call.time, tz)} · {part} · {call.tool_name}({params})"
    if call.reason:
        text += f' — "{" ".join(call.reason.split())}"'
    return text


async def _counts_table(redis: AioRedis, start: datetime, now: datetime) -> list[str]:
    lines = [
        "### Changes by cause",
        "",
        f"| Day (UTC) | {' | '.join(CAUSE_COLUMNS)} |",
        f"|---|{'---|' * len(CAUSE_COLUMNS)}",
    ]
    day = start.astimezone(UTC).date()
    while day <= now.astimezone(UTC).date():
        raw_counts = await redis.hgetall(causes_key(day))
        counts = {decode_stream_value(k): decode_stream_value(v) for k, v in raw_counts.items()}
        cells = " | ".join(counts.get(c, "0") for c in CAUSE_COLUMNS)
        lines.append(f"| {day.isoformat()} | {cells} |")
        day += timedelta(days=1)
    return lines


async def build_report(redis: AioRedis, *, days: int, now: datetime, tz_name: str) -> str:
    """Markdown: counts by cause, each call Alfred made with its changes, then everyone else's."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    start = now - timedelta(days=days)
    logged_calls = await _read(redis, ATTRIBUTION_CALLS_STREAM, "call", AlfredCall, start)
    calls = sorted((c for c in logged_calls if c.time >= start), key=lambda c: c.time)
    logged_changes = await _read(
        redis, ATTRIBUTION_CHANGES_STREAM, "change", AttributedChange, start
    )
    changes = sorted((c for c in logged_changes if c.time >= start), key=lambda c: c.time)

    # A change belongs to a call when it carries the call's context id (the call's own effect,
    # or a script it ran) or names it as its parent (an automation the call set off).
    calls_by_context: dict[str, list[int]] = {}
    for index, call in enumerate(calls):
        if call.context_id is not None:
            calls_by_context.setdefault(call.context_id, []).append(index)
    caused: dict[int, list[AttributedChange]] = {}
    matched: set[int] = set()
    unattributed = 0
    for position, change in enumerate(changes):
        context = change.context
        for context_id in (context.id, context.parent_id):
            for index in calls_by_context.get(context_id or "", ()):
                caused.setdefault(index, []).append(change)
                matched.add(position)
        # Only a change carrying the call's own id says the operator step is missing: a
        # script's or an automation's children are rightly an automation's work.
        if context.id is not None and context.id in calls_by_context and change.cause != "alfred":
            unattributed += 1

    lines: list[str] = []
    if unattributed:
        lines += [_MISMATCH.format(n=unattributed), ""]
    lines += [f"## Alfred attribution report: the last {days} days (times in {tz_name})", ""]
    lines += await _counts_table(redis, start, now)

    lines += ["", f"### What Alfred changed ({len(calls)})", ""]
    for index, call in enumerate(calls):
        lines.append(f"- {_call_text(call, tz)}")
        if call.context_id is None:
            lines.append("  - no context id (older home-service)")
        elif index not in caused:
            lines.append("  - no change seen")
        else:
            lines += [f"  - {_change_text(change, tz)}" for change in caused[index]]
    if not calls:
        lines.append(_NONE_IN_WINDOW)

    others = [
        change
        for position, change in enumerate(changes)
        if position not in matched and change.cause in ("person", "automation")
    ]
    lines += ["", f"### Changes by people and automations ({len(others)})", ""]
    lines += [f"- {_change_text(change, tz)}" for change in others] or [_NONE_IN_WINDOW]
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
    parser = argparse.ArgumentParser(description="Attribution report: who changed the house")
    parser.add_argument("--days", type=int, default=7, help="window length (default 7)")
    args = parser.parse_args()
    asyncio.run(_main(args.days))


if __name__ == "__main__":
    main()
