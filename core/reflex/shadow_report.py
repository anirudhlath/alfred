"""Shadow report: what Reflex proposed but did not do (#285).

    docker exec alfred python -m core.reflex.shadow_report --days 7

Prints Markdown for the owner's review: daily decision counts from
``alfred:reflex:decisions:<UTC date>``, then every act, ask and invalid proposal in
the window, oldest first, in the user's local time. The proposals name people, their
comings and goings and what was playing, so the report is reviewed privately: only
the counts and verdict tallies go to the public #285 thread.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError

from bus.schemas.events import ReflexObservation, StateChangedEvent
from core.reflex.prompt import render_event
from core.reflex.runner import decisions_key
from shared.config import AlfredConfig
from shared.redis_streams import create_redis, revrange
from shared.streams import REFLEX_OBSERVATIONS_STREAM, decode_stream_value
from shared.usertime import get_user_timezone

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from shared.types import AioRedis

DECISIONS: tuple[str, ...] = ("act", "ask", "none", "invalid")
# Upper bound on observations scanned. A week runs to a few thousand; past this the
# report says it was truncated rather than silently dropping the oldest.
MAX_SCAN = 20_000
MAX_RAW_CHARS = 200


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
    when = obs.timestamp.astimezone(tz).strftime("%a %d %b %H:%M")
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


async def build_report(redis: AioRedis, *, days: int, now: datetime, tz_name: str) -> str:
    """Markdown: per-day decision counts, then every non-none proposal in the window."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    start = now - timedelta(days=days)
    lines = [
        f"## Reflex shadow report: the last {days} days (times in {tz_name})",
        "",
        "| Day (UTC) | act | ask | none | invalid |",
        "|---|---|---|---|---|",
    ]
    day = start.astimezone(UTC).date()
    while day <= now.astimezone(UTC).date():
        raw_counts = await redis.hgetall(decisions_key(day))
        counts = {decode_stream_value(k): decode_stream_value(v) for k, v in raw_counts.items()}
        cells = " | ".join(counts.get(d, "0") for d in DECISIONS)
        lines.append(f"| {day.isoformat()} | {cells} |")
        day += timedelta(days=1)

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
