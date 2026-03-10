"""Root conftest — shared fixtures for the entire monorepo."""

from __future__ import annotations

import pytest

from bus.schemas.events import StateChangedEvent


@pytest.fixture(autouse=True)
def _clear_telemetry() -> None:
    """Clear the telemetry buffer before and after each test."""
    from sdk.alfred_sdk.telemetry import clear_telemetry_buffer

    clear_telemetry_buffer()
    yield  # type: ignore[misc]
    clear_telemetry_buffer()


@pytest.fixture
def tv_on_event() -> StateChangedEvent:
    """A TV turning on — the canonical test event."""
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room TV"},
    )
