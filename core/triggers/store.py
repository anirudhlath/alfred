"""TriggerStore — Redis CRUD + YAML snapshot/rehydration for triggers."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from core.triggers.registry import TriggerRegistry as TriggerRegistryType

from core.triggers.models import BaseTrigger  # noqa: TC001
from core.triggers.registry import TriggerRegistry
from shared.streams import TRIGGERS_KEY
from shared.types import AioRedis  # noqa: TC001

logger = logging.getLogger(__name__)


class TriggerStore:
    """Redis CRUD + YAML snapshot/rehydration."""

    def __init__(self, redis: AioRedis, snapshot_dir: Path | str) -> None:
        self._redis = redis
        self._snapshot_dir = Path(snapshot_dir)
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, BaseTrigger] = {}

    async def load(self) -> list[BaseTrigger]:
        """Load all triggers from Redis, falling back to disk if empty."""
        raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(  # type: ignore[misc]
            TRIGGERS_KEY
        )

        if raw:
            triggers = self._parse_redis_entries(raw)
            self._cache = {t.trigger_id: t for t in triggers}
            return triggers

        logger.info("Redis empty — rehydrating triggers from disk")
        triggers = self.rehydrate_from_disk_static(self._snapshot_dir, TriggerRegistry)
        for t in triggers:
            await self._redis.hset(  # type: ignore[misc]
                TRIGGERS_KEY, t.trigger_id, t.model_dump_json()
            )
        self._cache = {t.trigger_id: t for t in triggers}
        return triggers

    async def save(self, trigger: BaseTrigger) -> None:
        """Write to Redis + snapshot to YAML."""
        await self._redis.hset(  # type: ignore[misc]
            TRIGGERS_KEY, trigger.trigger_id, trigger.model_dump_json()
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._snapshot_to_yaml, trigger)
        self._cache[trigger.trigger_id] = trigger

    async def delete(self, trigger_id: str) -> None:
        """Remove from Redis + delete YAML file."""
        await self._redis.hdel(TRIGGERS_KEY, trigger_id)  # type: ignore[misc]
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_yaml, trigger_id)
        self._cache.pop(trigger_id, None)

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
        """Re-sync cache from Redis (safety net, called periodically).

        WARNING: Performs a full HGETALL + deserialization. Intended for the
        60-second background loop only — never call from the hot path.
        """
        raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(  # type: ignore[misc]
            TRIGGERS_KEY
        )
        self._cache = {t.trigger_id: t for t in self._parse_redis_entries(raw)}

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
