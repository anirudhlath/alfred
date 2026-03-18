"""TimeTrigger — fires on cron schedule or specific datetime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from croniter import croniter  # type: ignore[import-untyped]
from pydantic import BaseModel, PrivateAttr

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@TriggerRegistry.register_type("time")
class TimeTrigger(BaseTrigger):
    """Fires on a cron schedule or at a specific datetime."""

    trigger_type: str = "time"

    class Conditions(BaseModel):
        """Time-based trigger conditions."""

        cron: str | None = None
        run_at: datetime | None = None

    conditions: Conditions
    _validated_cron: croniter | None = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        """Validate cron expression at construction time (fail-fast).

        Note: model_copy(update=...) re-runs model_post_init in Pydantic v2 —
        tests verify this invariant (test_model_copy_rebuilds_validated_cron).
        """
        if self.conditions.cron is not None:
            try:
                self._validated_cron = croniter(self.conditions.cron)
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid cron expression {self.conditions.cron!r}: {e}") from e

    def evaluate(self, context: TriggerContext) -> bool:
        """Check if the current time matches the cron or run_at condition."""
        now = context.now

        if self.conditions.cron is not None:
            cron = croniter(self.conditions.cron, now - timedelta(seconds=1))
            next_fire: datetime = cron.get_next(datetime)
            diff = abs((next_fire - now).total_seconds())
            return bool(diff < 1.0)

        if self.conditions.run_at is not None:
            target = self.conditions.run_at
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            if now >= target:
                return self.last_fired is None or self.last_fired < target
            return False

        return False
