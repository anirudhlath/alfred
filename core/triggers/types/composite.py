"""CompositeTrigger — fires when N of M child conditions are met."""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003
from datetime import datetime  # noqa: TC003
from typing import Any, Self

from pydantic import BaseModel, PrivateAttr

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@TriggerRegistry.register_type("composite")
class CompositeTrigger(BaseTrigger):
    """Fires when at least `require` of the child conditions evaluate to True."""

    trigger_type: str = "composite"

    class Conditions(BaseModel):
        """Composite trigger conditions."""

        children: list[dict[str, Any]]
        require: int

    conditions: Conditions
    _cached_children: list[BaseTrigger] = PrivateAttr(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        """Pre-build child trigger instances at construction time.

        Note: Pydantic v2's `model_copy` does NOT call `model_post_init` — it
        takes a shallow copy of `__dict__`/`__pydantic_private__` instead, so
        `_cached_children` built here would go stale (still reflecting the
        pre-copy `last_fired`) after a `model_copy(update=...)`. We override
        `model_copy` below to rebuild children so the invariant tests exercise
        (test_model_copy_rebuilds_cached_children,
        test_children_inherit_parent_last_fired) actually hold.
        """
        children: list[BaseTrigger] = []
        for i, child_spec in enumerate(self.conditions.children):
            child_type = child_spec.get("trigger_type", "")
            child_conditions = child_spec.get("conditions", {})
            child_cls = TriggerRegistry.get(child_type)
            child = child_cls(
                trigger_id=f"{self.trigger_id}:child:{i}",
                trigger_type=child_type,
                name=f"{self.name}:child:{i}",
                created_by=self.created_by,
                created_at=self.created_at,
                last_fired=self.last_fired,
                conditions=child_conditions,
            )
            children.append(child)
        self._cached_children = children

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Rebuild `_cached_children` after copy (see `model_post_init` note).

        Without this override, `trigger.model_copy(update={"last_fired": now})`
        (the pattern `TriggerEngine.fire` uses to mark a fire) would leave
        cached children anchored on the pre-fire `last_fired`, so a cron child
        would re-fire on every subsequent scheduler wake instead of
        re-anchoring on the new fire time.
        """
        copied = super().model_copy(update=update, deep=deep)
        copied.model_post_init(None)
        return copied

    def evaluate(self, context: TriggerContext) -> bool:
        """Evaluate each cached child trigger and check if enough are satisfied."""
        matched = 0
        for child in self._cached_children:
            if child.evaluate(context):
                matched += 1
                if matched >= self.conditions.require:
                    return True
        return matched >= self.conditions.require

    def next_fire_time(self, context: TriggerContext) -> datetime | None:
        """Earliest clock candidate among children (None if none are clock-driven)."""
        candidates = [
            t for c in self._cached_children if (t := c.next_fire_time(context)) is not None
        ]
        return min(candidates, default=None)
