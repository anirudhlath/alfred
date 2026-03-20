"""TriggerFeature — CRUD tools for trigger management via BaseFeature."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from bus.schemas.events import TriggerCreated
from core.triggers.models import ActionPayload
from core.triggers.registry import TriggerRegistry
from sdk.alfred_sdk.feature import BaseFeature, ToolMeta, tool
from shared.streams import EVENTS_STREAM

if TYPE_CHECKING:
    from core.triggers.store import TriggerStore


class TriggerFeatureContext:
    """Context object passed to TriggerFeature on instantiation."""

    def __init__(self, store: TriggerStore, redis: Any = None) -> None:
        self.store = store
        self.redis = redis


class TriggerFeature(BaseFeature):
    """Manage dynamic triggers — create, list, update, delete."""

    feature_name = "triggers"

    def __init__(self, ctx: TriggerFeatureContext | None = None) -> None:
        if ctx is not None:
            self._store = ctx.store
            self._redis = ctx.redis
        else:
            self._store = None  # type: ignore[assignment]
            self._redis = None

    def get_tools(self) -> list[ToolMeta]:
        """Override to inject dynamic descriptions from TriggerRegistry."""
        tools = super().get_tools()
        conditions_docs = TriggerRegistry.build_conditions_docs()
        action_docs = (
            "\n\naction (optional): {tool_name: str, target_service: str, parameters: dict}\n"
            "If omitted, fires a TriggerFired event for the Reflex Engine to handle."
        )

        enriched: list[ToolMeta] = []
        for t in tools:
            if not isinstance(t.name, str):
                continue
            if "create_trigger" in t.name:
                enriched.append(
                    ToolMeta(
                        name=t.name,
                        description=t.description + "\n\n" + conditions_docs + action_docs,
                        parameters=t.parameters,
                    )
                )
            else:
                enriched.append(t)
        return enriched

    @tool
    async def create_trigger(
        self,
        name: str,
        trigger_type: str,
        conditions: dict[str, Any],
        action: dict[str, Any] | None = None,
        one_shot: bool = False,
    ) -> dict[str, Any]:
        """Create a new trigger. Use this for reminders, scheduled tasks, and automation rules."""
        try:
            cls = TriggerRegistry.get(trigger_type)
        except KeyError as e:
            return {"error": str(e)}

        try:
            validated_action = ActionPayload(**action) if action else None
        except Exception as e:
            return {"error": f"Invalid action: {e}"}

        try:
            trigger = cls(
                trigger_id=str(uuid4()),
                trigger_type=trigger_type,
                name=name,
                enabled=True,
                one_shot=one_shot,
                created_by="tool-call",
                created_at=datetime.now(UTC),
                action=validated_action,
                conditions=conditions,
            )
        except Exception as e:
            return {"error": f"Invalid conditions for type '{trigger_type}': {e}"}

        await self._store.save(trigger)

        # Publish TriggerCreated event to bus
        if self._redis is not None:
            event = TriggerCreated(
                trigger_id=trigger.trigger_id,
                trigger_type=trigger_type,
                name=name,
                created_by="tool-call",
                conditions=conditions,
                action=action,
                one_shot=one_shot,
            )
            await self._redis.xadd(EVENTS_STREAM, {"event": event.model_dump_json()})

        return trigger.model_dump(mode="json")

    @tool
    async def list_triggers(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """List all triggers."""
        triggers = await self._store.list_all(enabled_only=enabled_only)
        return [t.model_dump(mode="json") for t in triggers]

    @tool
    async def update_trigger(
        self,
        trigger_id: str,
        conditions: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing trigger's conditions, action, or name."""
        target = await self._store.get(trigger_id)
        if target is None:
            return {"error": f"Trigger '{trigger_id}' not found"}

        updates: dict[str, Any] = {}
        if name is not None:
            updates["name"] = name
        if conditions is not None:
            cls = TriggerRegistry.get(target.trigger_type)
            try:
                conditions_model = cls.Conditions  # type: ignore[attr-defined]
                validated = conditions_model(**conditions)
                updates["conditions"] = validated.model_dump(mode="json")
            except Exception as e:
                return {"error": f"Invalid conditions: {e}"}
        if action is not None:
            try:
                validated_action = ActionPayload(**action)
                updates["action"] = validated_action
            except Exception as e:
                return {"error": f"Invalid action: {e}"}

        updated = target.model_copy(update=updates)
        await self._store.save(updated)
        return updated.model_dump(mode="json")

    @tool
    async def delete_trigger(self, trigger_id: str) -> dict[str, str]:
        """Delete a trigger by ID."""
        await self._store.delete(trigger_id)
        return {"status": "deleted", "trigger_id": trigger_id}

    @tool
    async def toggle_trigger(self, trigger_id: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable a trigger."""
        target = await self._store.get(trigger_id)
        if target is None:
            return {"error": f"Trigger '{trigger_id}' not found"}

        updated = target.model_copy(update={"enabled": enabled})
        await self._store.save(updated)
        return updated.model_dump(mode="json")
