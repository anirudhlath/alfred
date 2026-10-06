"""read_live_state: what Alfred sees of every registered service's live state."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sdk.alfred_sdk.context import ContextEntry
from sdk.alfred_sdk.live_state import (
    LiveStateEntry,
    live_state_key,
    read_live_state,
    read_live_state_by_service,
)
from sdk.tests.fake_live_redis import FakeLiveRedis

if TYPE_CHECKING:
    import pytest

REGISTRY = "alfred:tool_registry"


def _entry(domain: str, kind: str, state: str, **attributes: object) -> str:
    return LiveStateEntry(
        domain=domain, kind=kind, state=state, attributes=attributes
    ).model_dump_json()


def _seed(fake: FakeLiveRedis, service: str, entries: dict[str, str]) -> None:
    fake.hashes.setdefault(REGISTRY, {})[service.encode()] = b"{}"
    if entries:
        fake.hashes[live_state_key(service)] = {
            field.encode(): value.encode() for field, value in entries.items()
        }


async def test_no_registered_service_reads_as_unavailable() -> None:
    assert await read_live_state(FakeLiveRedis()) is None


async def test_registered_services_without_state_read_as_unavailable() -> None:
    fake = FakeLiveRedis()
    _seed(fake, "home-service", {})
    assert await read_live_state(fake) is None


async def test_services_merge_into_one_snapshot() -> None:
    fake = FakeLiveRedis()
    _seed(
        fake,
        "home-service",
        {
            "light.b": _entry("light", "controllable", "off"),
            "light.a": _entry("light", "controllable", "on", brightness=10),
            "sensor.t": _entry("sensor", "sensor", "21"),
        },
    )
    _seed(fake, "garden-service", {"light.c": _entry("light", "controllable", "on")})

    snapshot = await read_live_state(fake)

    assert snapshot is not None
    assert [e.entity_id for e in snapshot.controllable["light"]] == [
        "light.c",
        "light.a",
        "light.b",
    ]
    assert snapshot.controllable["light"][1].attributes == {"brightness": 10}
    assert snapshot.sensors == {"sensor": [ContextEntry(entity_id="sensor.t", state="21")]}


async def test_by_service_keeps_services_apart() -> None:
    fake = FakeLiveRedis()
    _seed(fake, "home-service", {"sensor.t": _entry("sensor", "sensor", "21")})
    _seed(fake, "garden-service", {"light.c": _entry("light", "controllable", "on")})
    _seed(fake, "idle-service", {})

    by_service = await read_live_state_by_service(fake)

    assert list(by_service) == ["garden-service", "home-service"]
    assert by_service["garden-service"].sensors == {}


async def test_state_of_an_unregistered_service_is_not_read() -> None:
    fake = FakeLiveRedis()
    fake.hashes[live_state_key("ghost")] = {
        b"light.x": _entry("light", "controllable", "on").encode()
    }
    assert await read_live_state(fake) is None


async def test_malformed_values_are_skipped_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeLiveRedis()
    _seed(
        fake,
        "home-service",
        {
            "light.good": _entry("light", "controllable", "on"),
            "light.not_json": "{",
            "light.bad_kind": '{"domain": "light", "kind": "gadget", "state": "on"}',
            "light.extra": '{"domain": "light", "kind": "sensor", "state": "on", "x": 1}',
        },
    )
    caplog.set_level(logging.WARNING)

    snapshot = await read_live_state(fake)

    assert snapshot is not None
    assert [e.entity_id for e in snapshot.controllable["light"]] == ["light.good"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "3" in warnings[0].getMessage()


async def test_every_service_is_read_in_one_round_trip() -> None:
    fake = FakeLiveRedis()
    _seed(fake, "home-service", {"sensor.t": _entry("sensor", "sensor", "21")})
    _seed(fake, "garden-service", {"light.c": _entry("light", "controllable", "on")})

    await read_live_state(fake)

    assert fake.executes == [(False, ["hgetall", "hgetall"])]


def test_the_package_exports_the_writer_and_the_reader() -> None:
    import sdk.alfred_sdk as alfred_sdk

    assert alfred_sdk.LiveStateWriter.__name__ == "LiveStateWriter"
    assert alfred_sdk.read_live_state is read_live_state
