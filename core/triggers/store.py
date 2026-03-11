"""TriggerStore — Redis CRUD + YAML snapshot/rehydration for triggers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from core.triggers.registry import TriggerRegistry as TriggerRegistryType

from core.triggers.models import BaseTrigger  # noqa: TC001
from core.triggers.registry import TriggerRegistry

logger = logging.getLogger(__name__)

REDIS_KEY = "alfred:triggers"

AioRedis = Any


class TriggerStore:
    """Redis CRUD + YAML snapshot/rehydration."""

    def __init__(self, redis: AioRedis, snapshot_dir: Path | str) -> None:
        self._redis = redis
        self._snapshot_dir = Path(snapshot_dir)
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)

    async def load(self) -> list[BaseTrigger]:
        """Load all triggers from Redis, falling back to disk if empty."""
        raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(REDIS_KEY)

        if raw:
            return self._parse_redis_entries(raw)

        logger.info("Redis empty — rehydrating triggers from disk")
        triggers = self.rehydrate_from_disk_static(self._snapshot_dir, TriggerRegistry)
        for t in triggers:
            await self._redis.hset(REDIS_KEY, t.trigger_id, t.model_dump_json())
        return triggers

    async def save(self, trigger: BaseTrigger) -> None:
        """Write to Redis + snapshot to YAML."""
        await self._redis.hset(REDIS_KEY, trigger.trigger_id, trigger.model_dump_json())
        self._snapshot_to_yaml(trigger)

    async def delete(self, trigger_id: str) -> None:
        """Remove from Redis + delete YAML file."""
        await self._redis.hdel(REDIS_KEY, trigger_id)
        yaml_path = self._snapshot_dir / f"{trigger_id}.yaml"
        if yaml_path.exists():
            yaml_path.unlink()

    async def list_all(self, enabled_only: bool = False) -> list[BaseTrigger]:
        """Return all triggers, optionally filtered by enabled status."""
        raw: dict[str | bytes, str | bytes] = await self._redis.hgetall(REDIS_KEY)
        triggers = self._parse_redis_entries(raw)
        if enabled_only:
            return [t for t in triggers if t.enabled]
        return triggers

    async def snapshot_all(self) -> None:
        """Dump all triggers to YAML (periodic task)."""
        triggers = await self.list_all()
        for t in triggers:
            self._snapshot_to_yaml(t)

    def _snapshot_to_yaml(self, trigger: BaseTrigger) -> None:
        """Write a single trigger to YAML."""
        yaml_path = self._snapshot_dir / f"{trigger.trigger_id}.yaml"
        data = json.loads(trigger.model_dump_json())
        yaml_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

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
