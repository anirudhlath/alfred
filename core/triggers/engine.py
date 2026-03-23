# core/triggers/engine.py
"""TriggerEngine — evaluation loops and fire logic."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bus.schemas.events import ActionRequest, StateChangedEvent, TriggerFired
from core.triggers.models import BaseTrigger, TriggerContext
from shared.streams import ACTIONS_STREAM, EVENTS_STREAM, SCRATCHPAD_QUEUE
from shared.types import AioRedis  # noqa: TC001

if TYPE_CHECKING:
    from core.triggers.store import TriggerStore

logger = logging.getLogger(__name__)


class TriggerEngine:
    """Evaluates triggers and fires actions or events."""

    def __init__(self, store: TriggerStore, redis: AioRedis) -> None:
        self._store = store
        self._redis = redis

    async def fire(self, trigger: BaseTrigger, context: TriggerContext) -> None:
        """Execute the fire logic for a trigger that evaluated to True."""
        now = datetime.now(UTC)

        if trigger.action is not None:
            action = ActionRequest(
                source="trigger-engine",
                target_service=trigger.action.target_service,
                tool_name=trigger.action.tool_name,
                parameters=trigger.action.parameters,
            )
            await self._redis.xadd(ACTIONS_STREAM, {"event": action.model_dump_json()})
            logger.info("Trigger '%s' fired → ActionRequest %s", trigger.name, action.tool_name)
        else:
            event = TriggerFired(
                trigger_id=trigger.trigger_id,
                trigger_name=trigger.name,
                trigger_type=trigger.trigger_type,
                context=self._build_fire_context(trigger, context),
                urgency=trigger.urgency.value,
            )
            await self._redis.xadd(EVENTS_STREAM, {"event": event.model_dump_json()})
            logger.info("Trigger '%s' fired → TriggerFired event", trigger.name)

        observation = (
            f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"[trigger] {trigger.name} (type={trigger.trigger_type}) fired"
        )
        await self._redis.lpush(SCRATCHPAD_QUEUE, observation)

        if trigger.one_shot:
            await self._store.delete(trigger.trigger_id)
            logger.info("One-shot trigger '%s' deleted", trigger.name)
        else:
            updated = trigger.model_copy(update={"last_fired": now})
            await self._store.save(updated)

    async def evaluate_tick(self, now: datetime) -> None:
        """Evaluate all enabled triggers against the current time (tick loop)."""
        await self._evaluate_all(TriggerContext(now=now))

    async def evaluate_event(self, event: StateChangedEvent) -> None:
        """Evaluate all enabled triggers against an incoming event."""
        await self._evaluate_all(TriggerContext(now=datetime.now(UTC), event=event))

    async def _evaluate_all(self, context: TriggerContext) -> None:
        """Evaluate all enabled triggers against the given context."""
        triggers = await self._store.list_all(enabled_only=True)
        is_tick = context.event is None

        for trigger in triggers:
            if is_tick and not trigger.responds_to_tick:
                continue
            try:
                if trigger.evaluate(context):
                    await self.fire(trigger, context)
            except Exception as e:
                logger.error("Error evaluating trigger '%s': %s", trigger.trigger_id, e)

    def _build_fire_context(self, trigger: BaseTrigger, context: TriggerContext) -> dict[str, Any]:
        """Build the context dict for a TriggerFired event."""
        ctx: dict[str, Any] = {"trigger_type": trigger.trigger_type}
        if context.event is not None:
            ctx["event_entity"] = context.event.entity_id
            ctx["event_state"] = context.event.new_state
        ctx["evaluated_at"] = context.now.isoformat()
        return ctx
