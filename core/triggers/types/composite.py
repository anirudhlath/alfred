"""CompositeTrigger — fires when N of M child conditions are met."""

from __future__ import annotations

from typing import Any

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
        """Pre-build child trigger instances at construction time."""
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
                conditions=child_conditions,
            )
            children.append(child)
        self._cached_children = children

    def evaluate(self, context: TriggerContext) -> bool:
        """Evaluate each cached child trigger and check if enough are satisfied."""
        matched = 0
        for child in self._cached_children:
            if child.evaluate(context):
                matched += 1
                if matched >= self.conditions.require:
                    return True
        return matched >= self.conditions.require
