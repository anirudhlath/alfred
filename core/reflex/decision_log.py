"""The Reflex decision log: every decision that reached the model, kept with its prompt.

Slice 2 builds Reflex's evals from real decisions, so each one is kept whole: the event, who
caused it, the exact prompt the model saw and what Reflex proposed. It is private bookkeeping
on ``alfred:reflex:decision_log``, kept ``ATTRIBUTION_MAX_AGE``. Nothing here is an effect on
the house: shadow mode still executes nothing.

Writes are best-effort: a refused write logs one warning and never raises into Reflex, and
never changes the proposal it decided.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import BaseModel

from bus.schemas.events import ReflexProposal, StateChangedEvent
from core.attribution.cause import Cause, cause_of
from shared.redis_streams import append_capped, forward_range
from shared.streams import (
    ATTRIBUTION_MAX_AGE,
    REFLEX_DECISION_LOG_MAXLEN,
    REFLEX_DECISION_LOG_STREAM,
    decode_stream_value,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from bus.schemas.events import TriggerFired
    from shared.types import AioRedis

_FIELD = "decision"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DecisionRecord(BaseModel):
    """One decision: the event Reflex was asked about, its cause, the prompt and the proposal."""

    time: datetime
    origin: Literal["state_change", "trigger_fired"]
    event: dict[str, Any]  # the event's model_dump(mode="json")
    cause: Cause | None  # who caused a state change; None for a trigger or an unattributed event
    prompt: str
    proposal: ReflexProposal


class DecisionLog:
    """Appends each Reflex decision to the log and reads them back for replay."""

    def __init__(self, redis: AioRedis, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._redis = redis
        self._clock = clock

    async def record(
        self,
        origin: Literal["state_change", "trigger_fired"],
        event: StateChangedEvent | TriggerFired,
        prompt: str,
        proposal: ReflexProposal,
    ) -> None:
        """Keep one decision. Best-effort: never raises."""
        try:
            now = self._clock()
            record = DecisionRecord(
                time=now,
                origin=origin,
                event=event.model_dump(mode="json"),
                cause=cause_of(event.context) if isinstance(event, StateChangedEvent) else None,
                prompt=prompt,
                proposal=proposal,
            )
            await append_capped(
                self._redis,
                REFLEX_DECISION_LOG_STREAM,
                {_FIELD: record.model_dump_json()},
                maxlen=REFLEX_DECISION_LOG_MAXLEN,
                max_age=ATTRIBUTION_MAX_AGE,
                now=now,
            )
        except Exception as e:
            logger.warning("Reflex decision log failed ({}): {}", origin, e)

    async def read(self, *, since: datetime) -> list[DecisionRecord]:
        """Every kept decision made at or after *since*, oldest first.

        Filters on each record's own ``time``. An entry that cannot be parsed is skipped with
        a warning, so one bad row cannot break the reader.
        """
        records: list[DecisionRecord] = []
        for entry_id, fields in await forward_range(self._redis, REFLEX_DECISION_LOG_STREAM):
            raw = fields.get(_FIELD.encode()) or fields.get(_FIELD)
            try:
                if raw is None:
                    raise ValueError(f"no {_FIELD!r} field")
                record = DecisionRecord.model_validate_json(decode_stream_value(raw))
            except ValueError as e:
                entry = decode_stream_value(entry_id)
                logger.warning("Skipping unreadable decision log entry {}: {}", entry, e)
                continue
            if record.time >= since:
                records.append(record)
        return records
