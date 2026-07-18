"""TriggerStore — Redis CRUD + YAML snapshot/rehydration for triggers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.triggers.registry import TriggerRegistry as TriggerRegistryType

from core.triggers.models import BaseTrigger  # noqa: TC001
from core.triggers.registry import TriggerRegistry
from shared.streams import (
    TRIGGER_SYNC_OP_DELETED,
    TRIGGER_SYNC_OP_SAVED,
    TRIGGER_SYNC_OP_TZ_CHANGED,
    TRIGGERS_CHANGED_CHANNEL,
    TRIGGERS_KEY,
    decode_stream_value,
)
from shared.types import AioRedis  # noqa: TC001

logger = logging.getLogger(__name__)


class TriggerStore:
    """Redis CRUD + YAML snapshot/rehydration."""

    def __init__(self, redis: AioRedis, snapshot_dir: Path | str) -> None:
        self._redis = redis
        self._snapshot_dir = Path(snapshot_dir)
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, BaseTrigger] = {}
        self._on_change: list[Callable[[], None]] = []
        self._sync_task: asyncio.Task[None] | None = None

    async def load(self) -> list[BaseTrigger]:
        """Load all triggers from Redis, falling back to disk if empty."""
        raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(TRIGGERS_KEY)

        if raw:
            triggers = self._parse_redis_entries(raw)
            self._cache = {t.trigger_id: t for t in triggers}
            return triggers

        logger.info("Redis empty — rehydrating triggers from disk")
        triggers = self.rehydrate_from_disk_static(self._snapshot_dir, TriggerRegistry)
        for t in triggers:
            await self._redis.hset(TRIGGERS_KEY, t.trigger_id, t.model_dump_json())
        self._cache = {t.trigger_id: t for t in triggers}
        return triggers

    async def save(self, trigger: BaseTrigger) -> None:
        """Write to Redis + snapshot to YAML."""
        await self._redis.hset(TRIGGERS_KEY, trigger.trigger_id, trigger.model_dump_json())
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._snapshot_to_yaml, trigger)
        self._cache[trigger.trigger_id] = trigger
        await self._publish_change(TRIGGER_SYNC_OP_SAVED, trigger.trigger_id)
        self._notify_change()

    async def delete(self, trigger_id: str) -> None:
        """Remove from Redis + delete YAML file."""
        await self._redis.hdel(TRIGGERS_KEY, trigger_id)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_yaml, trigger_id)
        self._cache.pop(trigger_id, None)
        await self._publish_change(TRIGGER_SYNC_OP_DELETED, trigger_id)
        self._notify_change()

    async def get(self, trigger_id: str) -> BaseTrigger | None:
        """Fetch a single trigger by ID from in-memory cache."""
        return self._cache.get(trigger_id)

    async def list_all(self, enabled_only: bool = False) -> list[BaseTrigger]:
        """Return all triggers from in-memory cache, optionally filtered."""
        triggers = list(self._cache.values())
        if enabled_only:
            return [t for t in triggers if t.enabled]
        return triggers

    async def refresh(self) -> None:
        """Re-sync cache from Redis — the reconciliation net behind pub/sub.

        Pub/sub (`_sync_loop`) is now the primary path for cross-process cache
        coherence and fires near-instantly; this full HGETALL + deserialization
        remains as the 60-second safety net that heals any missed messages
        (e.g. a dropped connection). Never call from the hot path.
        """
        raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(TRIGGERS_KEY)
        self._cache = {t.trigger_id: t for t in self._parse_redis_entries(raw)}
        self._notify_change()

    def add_on_change(self, callback: Callable[[], None]) -> None:
        """Register a synchronous callback fired after any cache change.

        Callbacks MUST be synchronous, idempotent, and non-blocking. A local
        mutation may fire them twice — once synchronously from ``save``/``delete``
        and once again when this process receives its own pub/sub message on
        ``TRIGGERS_CHANGED_CHANNEL`` — so callbacks must tolerate redundant calls.
        """
        self._on_change.append(callback)

    def _notify_change(self) -> None:
        for callback in self._on_change:
            try:
                callback()
            except Exception:
                logger.exception("Trigger on_change callback failed")

    async def _publish_change(self, op: str, trigger_id: str) -> None:
        """Best-effort pub/sub poke; reconciliation refresh covers misses."""
        try:
            await self._redis.publish(  # type: ignore[misc,unused-ignore]
                TRIGGERS_CHANGED_CHANNEL,
                json.dumps({"op": op, "trigger_id": trigger_id}),
            )
        except Exception as e:
            logger.error("Trigger change publish failed: %s", e)

    async def start_sync(self) -> None:
        """Start the pub/sub subscriber keeping this cache coherent."""
        if self._sync_task is None:
            self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop_sync(self) -> None:
        if self._sync_task is not None:
            self._sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sync_task
            self._sync_task = None

    async def _sync_loop(self) -> None:
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(TRIGGERS_CHANGED_CHANNEL)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    await self._apply_sync_message(decode_stream_value(message["data"]))
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()  # type: ignore[no-untyped-call,unused-ignore]
                raise
            except Exception as e:
                logger.error("Trigger sync subscriber error: %s — resubscribing", e)
                with contextlib.suppress(Exception):
                    await pubsub.aclose()  # type: ignore[no-untyped-call,unused-ignore]
                await asyncio.sleep(1.0)
                # Heal anything missed while disconnected (also notifies).
                with contextlib.suppress(Exception):
                    await self.refresh()

    async def _apply_sync_message(self, raw: str) -> None:
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Malformed trigger sync message: %r", raw)
            return
        op = data.get("op")
        trigger_id = str(data.get("trigger_id", ""))
        if op == TRIGGER_SYNC_OP_SAVED:
            value: str | bytes | None = await self._redis.hget(  # type: ignore[misc,unused-ignore]
                TRIGGERS_KEY, trigger_id
            )
            if value is None:
                self._cache.pop(trigger_id, None)  # raced with a delete
            else:
                parsed = self._parse_redis_entries({trigger_id: value})
                if parsed:
                    self._cache[trigger_id] = parsed[0]
        elif op == TRIGGER_SYNC_OP_DELETED:
            self._cache.pop(trigger_id, None)
        elif op != TRIGGER_SYNC_OP_TZ_CHANGED:
            logger.warning("Unknown trigger sync op: %r", op)
            return
        self._notify_change()

    async def snapshot_all(self) -> None:
        """Dump all triggers to YAML (periodic task)."""
        triggers = await self.list_all()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._snapshot_many, triggers)

    def _snapshot_many(self, triggers: list[BaseTrigger]) -> None:
        """Write all triggers to YAML (runs in thread pool)."""
        for t in triggers:
            self._snapshot_to_yaml(t)

    def _snapshot_to_yaml(self, trigger: BaseTrigger) -> None:
        """Write a single trigger to YAML."""
        yaml_path = self._snapshot_dir / f"{trigger.trigger_id}.yaml"
        data = trigger.model_dump(mode="json")
        yaml_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    def _delete_yaml(self, trigger_id: str) -> None:
        """Delete a single YAML snapshot (runs in thread pool)."""
        yaml_path = self._snapshot_dir / f"{trigger_id}.yaml"
        if yaml_path.exists():
            yaml_path.unlink()

    def _parse_redis_entries(self, raw: dict[str | bytes, str | bytes]) -> list[BaseTrigger]:
        """Parse raw Redis hash entries into BaseTrigger instances."""
        triggers: list[BaseTrigger] = []
        for key, value in raw.items():
            val_str = value.decode() if isinstance(value, bytes) else value
            try:
                data: dict[str, Any] = json.loads(val_str)
                trigger_type = data.get("trigger_type", "")
                cls = TriggerRegistry.get(trigger_type)
                triggers.append(cls(**data))
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                tid = key.decode() if isinstance(key, bytes) else key
                logger.error("Failed to parse trigger '%s': %s", tid, e)
        return triggers

    @staticmethod
    def rehydrate_from_disk_static(
        snapshot_dir: Path, registry: type[TriggerRegistryType]
    ) -> list[BaseTrigger]:
        """Read all YAML files and return trigger instances."""
        triggers: list[BaseTrigger] = []
        if not snapshot_dir.exists():
            return triggers

        for yaml_path in snapshot_dir.glob("*.yaml"):
            try:
                data: dict[str, Any] = yaml.safe_load(yaml_path.read_text())
                trigger_type = data.get("trigger_type", "")
                cls = registry.get(trigger_type)
                triggers.append(cls(**data))
            except Exception as e:
                logger.error("Failed to load trigger from '%s': %s", yaml_path, e)

        return triggers
