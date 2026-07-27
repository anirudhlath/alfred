"""AttentionSet — lazy seeding, sticky removal, cooldown, transition-only gating."""

from __future__ import annotations

from typing import Any

import pytest

from bus.schemas.events import StateChangedEvent


class FakeSetRedis:
    """Minimal in-memory Redis supporting the SET commands AttentionSet uses."""

    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}

    async def sadd(self, key: str, member: str) -> int:
        self.sets.setdefault(key, set()).add(member)
        return 1

    async def srem(self, key: str, member: str) -> int:
        self.sets.get(key, set()).discard(member)
        return 1

    async def sismember(self, key: str, member: str) -> bool:
        return member in self.sets.get(key, set())

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))


def _event(
    entity_id: str,
    old: str | None = "off",
    new: str = "on",
    attributes: dict[str, Any] | None = None,
) -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id=entity_id,
        old_state=old,
        new_state=new,
        attributes=attributes or {},
    )


def _attention(redis: FakeSetRedis, cooldown: float = 0.0) -> Any:
    from core.reflex.attention import AttentionSet

    return AttentionSet(redis=redis, cooldown_seconds=cooldown)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_seeds_by_domain_prefix() -> None:
    """light.* is in the seed domains — first sight joins and fires."""
    redis = FakeSetRedis()
    attention = _attention(redis)
    assert await attention.should_fire(_event("light.kitchen")) is True
    assert "light.kitchen" in redis.sets["alfred:attention:home"]
    assert "light.kitchen" in redis.sets["alfred:attention:home:seen"]


@pytest.mark.asyncio
async def test_seeds_by_device_class() -> None:
    """binary_sensor is not a seed domain, but device_class=motion is."""
    redis = FakeSetRedis()
    attention = _attention(redis)
    event = _event("binary_sensor.hallway_motion", attributes={"device_class": "motion"})
    assert await attention.should_fire(event) is True
    assert "binary_sensor.hallway_motion" in redis.sets["alfred:attention:home"]


@pytest.mark.asyncio
async def test_non_matching_entity_is_gated_and_marked_seen() -> None:
    redis = FakeSetRedis()
    attention = _attention(redis)
    event = _event("sensor.dryer_power", old="100", new="150")
    assert await attention.should_fire(event) is False
    assert "sensor.dryer_power" not in redis.sets.get("alfred:attention:home", set())
    assert "sensor.dryer_power" in redis.sets["alfred:attention:home:seen"]


@pytest.mark.asyncio
async def test_removal_is_sticky_against_reseeding() -> None:
    """attention_remove marks the entity seen — the seed rule never re-adds it."""
    from core.reflex.attention import attention_remove

    redis = FakeSetRedis()
    attention = _attention(redis)
    assert await attention.should_fire(_event("light.bedroom")) is True
    await attention_remove(redis, "home", "light.bedroom")  # type: ignore[arg-type]
    assert await attention.should_fire(_event("light.bedroom", old="on", new="off")) is False


@pytest.mark.asyncio
async def test_add_and_list_helpers() -> None:
    from core.reflex.attention import attention_add, attention_list

    redis = FakeSetRedis()
    await attention_add(redis, "home", "sensor.dryer_power")  # type: ignore[arg-type]
    assert await attention_list(redis, "home") == ["sensor.dryer_power"]  # type: ignore[arg-type]
    # Manually added entities fire even though the seed rule would reject them
    attention = _attention(redis)
    assert await attention.should_fire(_event("sensor.dryer_power", old="0", new="900")) is True


@pytest.mark.asyncio
async def test_attribute_only_update_is_gated() -> None:
    """new_state == old_state (attribute-only forward per contract C11) never fires."""
    redis = FakeSetRedis()
    attention = _attention(redis)
    assert await attention.should_fire(_event("light.kitchen", old="on", new="on")) is False


@pytest.mark.asyncio
async def test_cooldown_collapses_bursts() -> None:
    redis = FakeSetRedis()
    attention = _attention(redis, cooldown=1000.0)
    assert await attention.should_fire(_event("light.kitchen")) is True
    assert await attention.should_fire(_event("light.kitchen", old="on", new="off")) is False


@pytest.mark.asyncio
async def test_cooldown_is_per_entity() -> None:
    redis = FakeSetRedis()
    attention = _attention(redis, cooldown=1000.0)
    assert await attention.should_fire(_event("light.kitchen")) is True
    assert await attention.should_fire(_event("light.bedroom")) is True


def test_seed_yaml_matches_contract() -> None:
    """The shipped seed file carries exactly the contract-C8 rules."""
    import yaml

    from core.reflex.attention import DEFAULT_SEED_PATH, AttentionSeedRules

    data = yaml.safe_load(DEFAULT_SEED_PATH.read_text())
    rules = AttentionSeedRules.model_validate(data)
    assert rules.domains == [
        "light",
        "switch",
        "media_player",
        "scene",
        "climate",
        "lock",
        "person",
    ]
    assert rules.device_classes == [
        "door",
        "motion",
        "occupancy",
        "presence",
        "window",
        "garage_door",
    ]
