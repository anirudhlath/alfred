# core/triggers/engine.py
"""TriggerEngine — evaluation loops and fire logic."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from bus.schemas.events import ActionRequest, StateChangedEvent, TriggerFired
from core.triggers.models import BaseTrigger, TriggerContext
from shared.streams import ACTIONS_STREAM, EVENTS_STREAM, SCRATCHPAD_QUEUE
from shared.types import AioRedis  # noqa: TC001
from shared.usertime import get_user_timezone

if TYPE_CHECKING:
    from core.triggers.store import TriggerStore

logger = logging.getLogger(__name__)


class TriggerEngine:
    """Evaluates triggers and fires actions or events."""

    def __init__(self, store: TriggerStore, redis: AioRedis) -> None:
        self._store = store
        self._redis = redis
        self._tz_cache: str | None = None

    def invalidate_tz_cache(self) -> None:
        """Forget the cached user timezone; next evaluation re-reads it."""
        self._tz_cache = None

    async def _user_tz(self) -> str:
        """User timezone, cached in memory.

        Invalidated via TriggerStore.add_on_change — tz changes ride the same
        coherence channel ("tz-changed" op), so any change wakes the process
        and clears this cache before the next evaluation pass.
        """
        if self._tz_cache is None:
            self._tz_cache = await get_user_timezone(self._redis)
        return self._tz_cache

    async def fire(
        self,
        trigger: BaseTrigger,
        context: TriggerContext,
        fired_by: Literal["engine", "admin"] = "engine",
    ) -> None:
        """Execute the fire logic for a trigger that evaluated to True.

        `fired_by` marks provenance: organic engine evaluation ("engine") vs.
        a manual admin-initiated fire ("admin"). It is propagated onto the
        emitted TriggerFired event so downstream pattern detection can
        distinguish manual fires from real conditions.
        """
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
                fired_by=fired_by,
            )
            await self._redis.xadd(EVENTS_STREAM, {"event": event.model_dump_json()})
            logger.info("Trigger '%s' fired → TriggerFired event", trigger.name)

        observation = (
            f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"[trigger] {trigger.name} (type={trigger.trigger_type}) fired"
        )
        await self._redis.lpush(SCRATCHPAD_QUEUE, observation)  # type: ignore[misc,unused-ignore]

        if trigger.one_shot:
            await self._store.delete(trigger.trigger_id)
            logger.info("One-shot trigger '%s' deleted", trigger.name)
        else:
            updated = trigger.model_copy(update={"last_fired": now})
            await self._store.save(updated)

    async def evaluate_tick(self, now: datetime) -> None:
        """Evaluate all enabled triggers against the current time (scheduler pass)."""
        tz = await self._user_tz()
        await self._evaluate_all(TriggerContext(now=now, tz=tz))

    async def evaluate_event(self, event: StateChangedEvent) -> None:
        """Evaluate all enabled triggers against an incoming event."""
        tz = await self._user_tz()
        await self._evaluate_all(TriggerContext(now=datetime.now(UTC), tz=tz, event=event))

    async def next_wakeup(self, now: datetime) -> datetime | None:
        """Earliest strictly-future clock candidate across enabled triggers.

        Past-due candidates are excluded on purpose: the scheduler evaluates
        before arming the alarm, so a past-due trigger either fired (and
        re-anchored) or is blocked on non-time conditions — in which case the
        event path, not the clock, will complete it.
        """
        tz = await self._user_tz()
        context = TriggerContext(now=now, tz=tz)
        result: datetime | None = None
        for trigger in await self._store.list_all(enabled_only=True):
            try:
                candidate = trigger.next_fire_time(context)
            except Exception as e:
                logger.error("next_fire_time failed for '%s': %s", trigger.trigger_id, e)
                continue
            if candidate is None or candidate <= now:
                continue
            if result is None or candidate < result:
                result = candidate
        return result

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
