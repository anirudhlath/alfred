"""SensorTrigger — fires when an event matches entity/state/attribute conditions."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


@TriggerRegistry.register_type("sensor")
class SensorTrigger(BaseTrigger):
    """Fires when an incoming event matches conditions."""

    responds_to_tick: ClassVar[bool] = False
    trigger_type: str = "sensor"

    class Conditions(BaseModel):
        """Sensor-based trigger conditions."""

        entity_id: str
        state_match: str | None = None
        attribute_match: dict[str, Any] | None = None

    conditions: Conditions

    def evaluate(self, context: TriggerContext) -> bool:
        """Check if the current event matches the sensor conditions."""
        if context.event is None:
            return False

        event = context.event

        if event.entity_id != self.conditions.entity_id:
            return False

        state_match = self.conditions.state_match
        if state_match is not None and event.new_state != state_match:
            return False

        if self.conditions.attribute_match is not None:
            for key, expected in self.conditions.attribute_match.items():
                if event.attributes.get(key) != expected:
                    return False

        return True
