"""CompositeTrigger — fires when N of M child conditions are met."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

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

    def evaluate(self, context: TriggerContext) -> bool:
        """Evaluate each child trigger and check if enough are satisfied."""
        matched = 0

        for child_spec in self.conditions.children:
            child_type = child_spec.get("trigger_type", "")
            child_conditions = child_spec.get("conditions", {})

            child_cls = TriggerRegistry.get(child_type)
            child = child_cls(
                trigger_id=f"{self.trigger_id}:child",
                trigger_type=child_type,
                name=f"{self.name}:child",
                created_by=self.created_by,
                created_at=self.created_at,
                conditions=child_conditions,
            )

            if child.evaluate(context):
                matched += 1
                if matched >= self.conditions.require:
                    return True

        return matched >= self.conditions.require
