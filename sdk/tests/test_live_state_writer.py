"""LiveStateWriter: the format it writes and the order it writes in."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot
from sdk.alfred_sdk.live_state import (
    WRITE_TIMEOUT_SECONDS,
    LiveStateEntry,
    LiveStateWriter,
    live_state_key,
)
from sdk.tests.fake_live_redis import FakeLiveRedis

KEY = live_state_key("home-service")

SNAPSHOT = ContextSnapshot(
    controllable={
        "light": [ContextEntry(entity_id="light.lamp", state="on", attributes={"brightness": 128})]
    },
    sensors={"sensor": [ContextEntry(entity_id="sensor.temp", state="21.5")]},
)


def _writer(fake: FakeLiveRedis) -> LiveStateWriter:
    with patch("redis.asyncio.from_url", return_value=fake):
        return LiveStateWriter("redis://unused", "home-service")


def _stored(fake: FakeLiveRedis) -> dict[str, LiveStateEntry]:
    return {
        field.decode(): LiveStateEntry.model_validate_json(value)
        for field, value in fake.hashes.get(KEY, {}).items()
    }


def test_the_key_is_owned_by_the_sdk() -> None:
    assert KEY == "alfred:live_state:home-service"


async def test_replace_writes_one_field_per_entity_with_its_kind() -> None:
    fake = FakeLiveRedis()
    await _writer(fake).replace(SNAPSHOT)
    assert _stored(fake) == {
        "light.lamp": LiveStateEntry(
            domain="light", kind="controllable", state="on", attributes={"brightness": 128}
        ),
        "sensor.temp": LiveStateEntry(domain="sensor", kind="sensor", state="21.5"),
    }


async def test_replace_drops_entities_the_snapshot_no_longer_has() -> None:
    fake = FakeLiveRedis()
    writer = _writer(fake)
    await writer.update("light", "controllable", ContextEntry(entity_id="light.gone", state="on"))
    await writer.replace(SNAPSHOT)
    assert "light.gone" not in _stored(fake)


async def test_replace_is_one_transaction() -> None:
    fake = FakeLiveRedis()
    await _writer(fake).replace(SNAPSHOT)
    assert fake.executes == [(True, ["delete", "hset"])]


async def test_replace_with_an_empty_snapshot_leaves_no_hash() -> None:
    fake = FakeLiveRedis()
    writer = _writer(fake)
    await writer.replace(SNAPSHOT)
    await writer.replace(ContextSnapshot())
    assert KEY not in fake.hashes
    assert fake.executes[-1] == (True, ["delete"])


async def test_an_entry_that_cannot_be_stored_writes_nothing() -> None:
    fake = FakeLiveRedis()
    writer = _writer(fake)
    await writer.replace(SNAPSHOT)
    bad = ContextSnapshot(
        controllable={
            "light": [ContextEntry(entity_id="light.x", state="on", attributes={"x": object()})]
        }
    )
    with pytest.raises(PydanticSerializationError):
        await writer.replace(bad)
    assert set(_stored(fake)) == {"light.lamp", "sensor.temp"}


async def test_update_writes_one_field() -> None:
    fake = FakeLiveRedis()
    await _writer(fake).update(
        "light", "controllable", ContextEntry(entity_id="light.lamp", state="off")
    )
    assert _stored(fake) == {
        "light.lamp": LiveStateEntry(domain="light", kind="controllable", state="off")
    }


async def test_update_rejects_an_unknown_kind() -> None:
    fake = FakeLiveRedis()
    with pytest.raises(ValidationError):
        await _writer(fake).update(
            "light", "gadget", ContextEntry(entity_id="light.lamp", state="on")
        )
    assert fake.hashes == {}


async def test_remove_deletes_one_field() -> None:
    fake = FakeLiveRedis()
    writer = _writer(fake)
    await writer.replace(SNAPSHOT)
    await writer.remove("light.lamp")
    assert set(_stored(fake)) == {"sensor.temp"}


async def test_clear_deletes_the_hash() -> None:
    fake = FakeLiveRedis()
    writer = _writer(fake)
    await writer.replace(SNAPSHOT)
    await writer.clear()
    assert KEY not in fake.hashes


async def test_writes_land_in_the_order_they_were_requested() -> None:
    fake = FakeLiveRedis()
    writer = _writer(fake)
    fake.slow_next_hset = 0.05
    first = asyncio.create_task(
        writer.update("light", "controllable", ContextEntry(entity_id="light.lamp", state="on"))
    )
    await asyncio.sleep(0)  # `first` is now inside its slow HSET
    second = asyncio.create_task(
        writer.update("light", "controllable", ContextEntry(entity_id="light.lamp", state="off"))
    )
    await asyncio.gather(first, second)
    assert _stored(fake)["light.lamp"].state == "off"


def test_the_client_has_bounded_timeouts() -> None:
    with patch("redis.asyncio.from_url", return_value=FakeLiveRedis()) as from_url:
        LiveStateWriter("redis://h:6379/0", "svc")
    from_url.assert_called_once_with(
        "redis://h:6379/0",
        socket_timeout=WRITE_TIMEOUT_SECONDS,
        socket_connect_timeout=WRITE_TIMEOUT_SECONDS,
    )
    assert WRITE_TIMEOUT_SECONDS == 5.0


async def test_aclose_closes_the_client() -> None:
    fake = FakeLiveRedis()
    await _writer(fake).aclose()
    assert fake.closed
