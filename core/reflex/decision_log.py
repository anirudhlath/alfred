"""The Reflex decision log: every decision that reached the model, kept with its prompt.

Slice 2 builds Reflex's evals from real decisions, so each one is kept whole: the event, who
caused it, the exact prompt the model saw, what Reflex proposed, what that came to (its outcome)
and what people had just changed when it decided. It is private bookkeeping
on ``alfred:reflex:decision_log``, kept ``ATTRIBUTION_MAX_AGE``. Nothing here is an effect on
the house: shadow mode still executes nothing.

Writes are best-effort: a refused write logs one warning and never raises into Reflex, and
never changes the proposal it decided.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Literal

from loguru import logger
from pydantic import BaseModel

from bus.schemas.events import ReflexOutcome, ReflexProposal, StateChangedEvent
from core.attribution.cause import Cause, cause_of
from core.reflex.reversal import PersonChange  # noqa: TC001  # pydantic field type
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
    from core.reflex.outcome import Decided
    from shared.types import AioRedis

_FIELD = "decision"

# How far past ``until`` ``read`` asks Redis for entries. ``record`` stamps a record's time before
# its XADD and Redis assigns the id after, so an id can lead the time by the write's latency;
# the time filter, not the id, decides which of them belong to the window.
UNTIL_SLACK_MS: Final = 5_000


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ms(moment: datetime) -> int:
    """The unix milliseconds a stream id leads with."""
    return int(moment.timestamp() * 1000)


class DecisionRecord(BaseModel):
    """One decision: the event Reflex was asked about, its cause, the prompt and the proposal.

    ``outcome`` and ``recent`` came later: a record written before them has neither.
    """

    time: datetime
    origin: Literal["state_change", "trigger_fired"]
    event: dict[str, Any]  # the event's model_dump(mode="json")
    cause: Cause | None  # who caused a state change; None for a trigger or an unattributed event
    prompt: str
    proposal: ReflexProposal
    outcome: ReflexOutcome | None = None  # what the proposal came to
    recent: dict[str, PersonChange] = {}  # what people had just changed, by entity id


class DecisionLog:
    """Appends each Reflex decision to the log and reads them back for replay."""

    def __init__(self, redis: AioRedis, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._redis = redis
        self._clock = clock

    async def record(
        self,
        origin: Literal["state_change", "trigger_fired"],
        event: StateChangedEvent | TriggerFired,
        decided: Decided,
    ) -> None:
        """Keep one decision. Best-effort: never raises."""
        try:
            now = self._clock()
            record = DecisionRecord(
                time=now,
                origin=origin,
                event=event.model_dump(mode="json"),
                cause=cause_of(event.context) if isinstance(event, StateChangedEvent) else None,
                prompt=decided.prompt,
                proposal=decided.proposal,
                outcome=decided.outcome,
                recent=decided.recent,
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

    async def read(self, *, since: datetime, until: datetime | None = None) -> list[DecisionRecord]:
        """Every kept decision made at or after *since* and before *until*, oldest first.

        Both bounds are timezone-aware. A record's time is stamped before its XADD, so its id
        (which leads with the unix-ms Redis wrote it at) can be later than its time by the
        write's latency. Redis is therefore asked for the ids from *since* to
        ``UNTIL_SLACK_MS`` past *until* (to the end of the stream without *until*), and each
        record is kept by its own ``time``: ``since <= time < until``. An entry that cannot be
        parsed is skipped with a warning, so one bad row cannot break the reader.

        Raises:
            ValueError: *since* or *until* is naive.
        """
        for name, bound in (("since", since), ("until", until)):
            if bound is not None and bound.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware, got the naive {bound!r}")
        # A bare id is completed by Redis: `<ms>-0` as a start, `<ms>-<max>` as an end.
        entries = await forward_range(
            self._redis,
            REFLEX_DECISION_LOG_STREAM,
            min_id=str(_ms(since)),
            max_id="+" if until is None else str(_ms(until) + UNTIL_SLACK_MS),
        )
        records: list[DecisionRecord] = []
        for entry_id, fields in entries:
            raw = fields.get(_FIELD.encode()) or fields.get(_FIELD)
            try:
                if raw is None:
                    raise ValueError(f"no {_FIELD!r} field")
                record = DecisionRecord.model_validate_json(decode_stream_value(raw))
            except ValueError as e:
                entry = decode_stream_value(entry_id)
                logger.warning("Skipping unreadable decision log entry {}: {}", entry, e)
                continue
            if record.time >= since and (until is None or record.time < until):
                records.append(record)
        return records
