"""TriggerFeature — CRUD tools for trigger management via BaseFeature."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from bus.schemas.events import TriggerCreated
from core.notifications.schema import Urgency
from core.triggers.models import ActionPayload
from core.triggers.registry import TriggerRegistry
from sdk.alfred_sdk.feature import BaseFeature, ToolMeta, tool
from shared.streams import EVENTS_STREAM
from shared.usertime import get_user_timezone

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
        self._store: TriggerStore | None
        if ctx is not None:
            self._store = ctx.store
            self._redis = ctx.redis
        else:
            self._store = None
            self._redis = None

    @property
    def _store_or_raise(self) -> TriggerStore:
        """Return the store or raise if TriggerFeature was created without context."""
        if self._store is None:
            raise RuntimeError("TriggerFeature used without TriggerFeatureContext")
        return self._store

    def get_tools(self) -> list[ToolMeta]:
        """Override to inject dynamic descriptions from TriggerRegistry."""
        tools = super().get_tools()
        conditions_docs = TriggerRegistry.build_conditions_docs()
        action_docs = (
            "\n\naction (optional): {tool_name: str, target_service: str, parameters: dict}\n"
            "If omitted, fires a TriggerFired event for the Reflex Engine to handle."
        )
        urgency_docs = (
            '\n\nurgency (optional): "informational" | "important" | "urgent"\n'
            "Sets notification urgency when trigger fires without an action. "
            "Default: informational."
        )

        enriched: list[ToolMeta] = []
        for t in tools:
            if not isinstance(t.name, str):
                continue
            if "create_trigger" in t.name:
                enriched.append(
                    ToolMeta(
                        name=t.name,
                        description=(
                            t.description + "\n\n" + conditions_docs + action_docs + urgency_docs
                        ),
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
        urgency: str = "informational",
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
            validated_urgency = Urgency(urgency)
        except ValueError:
            return {
                "error": f"Invalid urgency: {urgency}. Must be: informational, important, urgent"
            }

        try:
            tz_name = "UTC" if self._redis is None else await get_user_timezone(self._redis)
            normalized = cls.normalize_conditions(conditions, tz_name)
            trigger = cls(
                trigger_id=str(uuid4()),
                trigger_type=trigger_type,
                name=name,
                enabled=True,
                one_shot=one_shot,
                created_by="tool-call",
                created_at=datetime.now(UTC),
                action=validated_action,
                urgency=validated_urgency,
                conditions=normalized,
            )
        except Exception as e:
            return {"error": f"Invalid conditions for type '{trigger_type}': {e}"}

        await self._store_or_raise.save(trigger)

        # Publish TriggerCreated event to bus
        if self._redis is not None:
            event = TriggerCreated(
                trigger_id=trigger.trigger_id,
                trigger_type=trigger_type,
                name=name,
                created_by="tool-call",
                conditions=normalized,
                action=action,
                one_shot=one_shot,
                urgency=validated_urgency.value,
            )
            await self._redis.xadd(EVENTS_STREAM, {"event": event.model_dump_json()})

        return trigger.model_dump(mode="json")

    @tool
    async def list_triggers(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """List all triggers."""
        triggers = await self._store_or_raise.list_all(enabled_only=enabled_only)
        return [t.model_dump(mode="json") for t in triggers]

    @tool
    async def update_trigger(
        self,
        trigger_id: str,
        conditions: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        name: str | None = None,
        urgency: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing trigger's conditions, action, name, or urgency."""
        target = await self._store_or_raise.get(trigger_id)
        if target is None:
            return {"error": f"Trigger '{trigger_id}' not found"}

        updates: dict[str, Any] = {}
        if name is not None:
            updates["name"] = name
        if conditions is not None:
            cls = TriggerRegistry.get(target.trigger_type)
            try:
                tz_name = "UTC" if self._redis is None else await get_user_timezone(self._redis)
                normalized = cls.normalize_conditions(conditions, tz_name)
                conditions_model = cls.Conditions  # type: ignore[attr-defined]
                validated = conditions_model(**normalized)
                updates["conditions"] = validated
            except Exception as e:
                return {"error": f"Invalid conditions: {e}"}
        if action is not None:
            try:
                validated_action = ActionPayload(**action)
                updates["action"] = validated_action
            except Exception as e:
                return {"error": f"Invalid action: {e}"}
        if urgency is not None:
            try:
                updates["urgency"] = Urgency(urgency)
            except ValueError:
                return {
                    "error": (
                        f"Invalid urgency: {urgency}. Must be: informational, important, urgent"
                    )
                }

        updated = target.model_copy(update=updates)
        await self._store_or_raise.save(updated)
        return updated.model_dump(mode="json")

    @tool
    async def delete_trigger(self, trigger_id: str) -> dict[str, str]:
        """Delete a trigger by ID."""
        await self._store_or_raise.delete(trigger_id)
        return {"status": "deleted", "trigger_id": trigger_id}

    @tool
    async def toggle_trigger(self, trigger_id: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable a trigger."""
        target = await self._store_or_raise.get(trigger_id)
        if target is None:
            return {"error": f"Trigger '{trigger_id}' not found"}

        updated = target.model_copy(update={"enabled": enabled})
        await self._store_or_raise.save(updated)
        return updated.model_dump(mode="json")
