"""TimeTrigger — fires on cron schedule or specific datetime."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, PrivateAttr

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@TriggerRegistry.register_type("time")
class TimeTrigger(BaseTrigger):
    """Fires on a cron schedule or at a specific datetime."""

    trigger_type: str = "time"

    class Conditions(BaseModel):
        """Time-based trigger conditions."""

        cron: str | None = Field(
            default=None,
            description="5-field cron schedule, evaluated in the user's local timezone",
        )
        run_at: datetime | None = Field(
            default=None,
            description=(
                "Absolute due time, ISO-8601 WITH UTC offset "
                "(e.g. 2026-07-16T15:00:00-06:00). Naive values are interpreted "
                "in the user's local timezone."
            ),
        )

    conditions: Conditions
    _validated_cron: croniter | None = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        """Validate cron expression at construction time (fail-fast).

        Note: `_validated_cron` is validation-only — evaluation builds a fresh
        croniter per call. Pydantic 2.13's model_copy does NOT re-run
        model_post_init; copies inherit the private attr by shallow copy
        (see CompositeTrigger.model_copy for the case where that matters).
        """
        if self.conditions.cron is not None:
            try:
                self._validated_cron = croniter(self.conditions.cron)
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid cron expression {self.conditions.cron!r}: {e}") from e

    def _aware_run_at(self) -> datetime | None:
        """run_at as an aware datetime. Legacy naive values (pre-timezone data,
        computed against a UTC prompt) are interpreted as UTC; new writes are
        normalized at the tool boundary and always carry an offset."""
        target = self.conditions.run_at
        if target is None:
            return None
        if target.tzinfo is None:
            return target.replace(tzinfo=UTC)
        return target

    def next_fire_time(self, context: TriggerContext) -> datetime | None:
        run_at = self._aware_run_at()
        if run_at is not None:
            if self.last_fired is not None and self.last_fired >= run_at:
                return None
            return run_at
        if self.conditions.cron is not None:
            anchor = self.last_fired or self.created_at
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=UTC)
            local_anchor = anchor.astimezone(ZoneInfo(context.tz))
            # Compute in naive wall-clock space, then attach the zone: croniter's
            # aware-datetime handling does interval arithmetic across DST jumps
            # (7am becomes 6am after spring-forward) and varies by version — the
            # contract here is wall-clock ("0 7 * * *" means 7am local, always).
            naive_next: datetime = croniter(
                self.conditions.cron, local_anchor.replace(tzinfo=None)
            ).get_next(datetime)
            return naive_next.replace(tzinfo=ZoneInfo(context.tz))
        return None

    def evaluate(self, context: TriggerContext) -> bool:
        """Fire when the computed next fire time has been reached.

        Cron: next boundary strictly after (last_fired or created_at) — a late
        wakeup fires exactly once, then re-anchors. Replaces the old <1s
        tick-window match, which silently skipped fires on a busy loop.
        """
        target = self.next_fire_time(context)
        return target is not None and context.now >= target

    @classmethod
    def normalize_conditions(cls, conditions: dict[str, Any], tz_name: str) -> dict[str, Any]:
        run_at = conditions.get("run_at")
        if run_at is None:
            return conditions
        dt = (
            run_at
            if isinstance(run_at, datetime)
            else datetime.fromisoformat(str(run_at).replace("Z", "+00:00"))
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return {**conditions, "run_at": dt.isoformat()}
