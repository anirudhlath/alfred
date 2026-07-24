"""Attention set — gates which entities may fire the Reflex SLM.

Membership is lazily seeded from YAML rules (``attention_seed.yaml``) the
first time an entity is seen, then persisted in Redis sets under
``alfred:attention:{domain}``. A companion ``...:seen`` set records every
evaluated entity so runtime removals are sticky (a demoted entity is never
re-seeded). Triggers and context IGNORE the attention set — full visibility
is preserved; this gate applies only to the Reflex SLM path.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from loguru import logger
from pydantic import BaseModel, Field

from shared.streams import ATTENTION_PREFIX, decode_stream_value

if TYPE_CHECKING:
    from bus.schemas.events import StateChangedEvent
    from shared.types import AioRedis

DEFAULT_SEED_PATH = Path(__file__).resolve().parent / "attention_seed.yaml"
COOLDOWN_SECONDS = 5.0


class AttentionSeedRules(BaseModel):
    """Data-driven seed rules — domains + device classes that auto-join."""

    domains: list[str] = Field(default_factory=list)
    device_classes: list[str] = Field(default_factory=list)


def attention_key(domain: str) -> str:
    """Redis SET of attention-set members for a domain."""
    return f"{ATTENTION_PREFIX}{domain}"


def attention_seen_key(domain: str) -> str:
    """Redis SET of every entity ever evaluated (makes removals sticky)."""
    return f"{ATTENTION_PREFIX}{domain}:seen"


async def attention_add(redis: AioRedis, domain: str, entity_id: str) -> None:
    """Add an entity to the attention set (runtime primitive)."""
    await redis.sadd(attention_key(domain), entity_id)
    await redis.sadd(attention_seen_key(domain), entity_id)


async def attention_remove(redis: AioRedis, domain: str, entity_id: str) -> None:
    """Remove an entity — sticky: the seed rule will not re-add it."""
    await redis.srem(attention_key(domain), entity_id)
    await redis.sadd(attention_seen_key(domain), entity_id)


async def attention_list(redis: AioRedis, domain: str) -> list[str]:
    """Return sorted attention-set members for a domain."""
    members: set[bytes | str] = await redis.smembers(attention_key(domain))
    return sorted(decode_stream_value(m) for m in members)


class AttentionSet:
    """Decides whether a StateChangedEvent should reach the Reflex SLM."""

    def __init__(
        self,
        redis: AioRedis,
        seed_path: Path | None = None,
        cooldown_seconds: float = COOLDOWN_SECONDS,
    ) -> None:
        self._redis = redis
        self._seed_path = seed_path or DEFAULT_SEED_PATH
        self._cooldown = cooldown_seconds
        self._last_fired: dict[str, float] = {}
        self._rules: AttentionSeedRules | None = None

    def _load_rules(self) -> AttentionSeedRules:
        """Load seed rules; a missing/invalid file means nothing auto-joins."""
        try:
            data = yaml.safe_load(self._seed_path.read_text()) or {}
            return AttentionSeedRules.model_validate(data)
        except (OSError, ValueError) as exc:
            logger.warning("Attention seed rules unavailable ({}): {}", self._seed_path, exc)
            return AttentionSeedRules()

    def _seed_rules(self) -> AttentionSeedRules:
        if self._rules is None:
            self._rules = self._load_rules()
        return self._rules

    def _matches_seed(self, event: StateChangedEvent) -> bool:
        rules = self._seed_rules()
        entity_domain = event.entity_id.split(".", 1)[0]
        if entity_domain in rules.domains:
            return True
        device_class = event.attributes.get("device_class")
        return isinstance(device_class, str) and device_class in rules.device_classes

    async def _is_member(self, event: StateChangedEvent) -> bool:
        """Membership check with lazy first-sight seeding."""
        key = attention_key(event.domain)
        if await self._redis.sismember(key, event.entity_id):
            return True
        if await self._redis.sismember(attention_seen_key(event.domain), event.entity_id):
            return False  # evaluated before — respects runtime removals
        joins = self._matches_seed(event)
        await self._redis.sadd(attention_seen_key(event.domain), event.entity_id)
        if joins:
            await self._redis.sadd(key, event.entity_id)
            logger.info("Attention set: seeded {} into {}", event.entity_id, key)
        return joins

    async def should_fire(self, event: StateChangedEvent) -> bool:
        """True iff the Reflex SLM should process this event."""
        if event.new_state == event.old_state:
            return False  # attribute-only update — not a real transition
        if not await self._is_member(event):
            return False
        now = time.monotonic()
        last = self._last_fired.get(event.entity_id)
        if last is not None and (now - last) < self._cooldown:
            logger.debug("Attention cooldown: {} suppressed", event.entity_id)
            return False
        self._last_fired[event.entity_id] = now
        return True
