"""read_live_state: what Alfred sees of every registered service's live state."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot
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


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


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
    assert _warnings(caplog) == ["Skipped 3 malformed live-state item(s) from home-service"]


async def test_an_undecodable_entity_id_is_skipped_as_malformed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeLiveRedis()
    _seed(fake, "home-service", {"light.good": _entry("light", "controllable", "on")})
    fake.hashes[live_state_key("home-service")][b"light.\xff"] = _entry(
        "light", "controllable", "on"
    ).encode()
    caplog.set_level(logging.WARNING)

    snapshot = await read_live_state(fake)

    assert snapshot is not None
    assert [e.entity_id for e in snapshot.controllable["light"]] == ["light.good"]
    assert _warnings(caplog) == ["Skipped 1 malformed live-state item(s) from home-service"]


async def test_an_undecodable_service_name_is_skipped_and_not_fetched(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeLiveRedis()
    _seed(
        fake,
        "attic-service",
        {"sensor.t": _entry("sensor", "sensor", "21"), "sensor.bad": "{"},
    )
    # Found first (in the registry pass) but sorted last, so only the sort orders the names.
    fake.hashes[REGISTRY][b"\xff-service"] = b"{}"
    caplog.set_level(logging.WARNING)

    by_service = await read_live_state_by_service(fake)

    assert list(by_service) == ["attic-service"]
    assert fake.executes == [(False, ["hgetall"])]
    assert _warnings(caplog) == [
        "Skipped 2 malformed live-state item(s) from attic-service, b'\\xff-service'"
    ]


async def test_a_registry_of_only_undecodable_names_still_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeLiveRedis()
    fake.hashes[REGISTRY] = {b"\xff-service": b"{}"}
    caplog.set_level(logging.WARNING)

    assert await read_live_state(fake) is None
    assert fake.executes == []
    assert _warnings(caplog) == ["Skipped 1 malformed live-state item(s) from b'\\xff-service'"]


async def test_a_client_that_decodes_responses_reads_the_same() -> None:
    fake = FakeLiveRedis()
    # As from a client built with decode_responses=True: str keys, fields and values.
    fake.hashes[REGISTRY] = {"home-service": "{}"}
    fake.hashes[live_state_key("home-service")] = {
        "light.a": _entry("light", "controllable", "on", brightness=10),
        "sensor.t": _entry("sensor", "sensor", "21"),
    }

    snapshot = await read_live_state(fake)

    assert snapshot == ContextSnapshot(
        controllable={
            "light": [ContextEntry(entity_id="light.a", state="on", attributes={"brightness": 10})]
        },
        sensors={"sensor": [ContextEntry(entity_id="sensor.t", state="21")]},
    )


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
