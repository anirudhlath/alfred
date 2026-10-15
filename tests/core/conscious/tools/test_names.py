"""The one mapping from a tool's identity to the name the model sees (#299)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from core.conscious.tools import names
from core.conscious.tools.names import (
    MAX_WIRE_NAME,
    WireNameError,
    error_once,
    report_collision,
    wire_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _fresh_error_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(names, "_reported", set())
    yield


@pytest.mark.parametrize(
    ("identity", "wire"),
    [
        ("home.light_turn_on", "home_light_turn_on"),
        ("triggers.create_trigger", "triggers_create_trigger"),
        ("memory_recall_memories", "memory_recall_memories"),
        ("a b/c:d-e", "a_b_c_d-e"),
    ],
)
def test_wire_name_maps_every_forbidden_character_to_underscore(identity: str, wire: str) -> None:
    assert wire_name(identity) == wire


def test_wire_name_refuses_an_empty_name() -> None:
    with pytest.raises(WireNameError, match="cannot be empty"):
        wire_name("")


def test_wire_name_refuses_a_name_over_the_limit() -> None:
    assert MAX_WIRE_NAME == 64
    assert wire_name("x" * 64) == "x" * 64
    with pytest.raises(WireNameError, match="65-character name; the limit is 64"):
        wire_name("x" * 65)


def test_error_once_logs_each_key_once(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="core.conscious.tools.names"):
        for _ in range(3):
            error_once(("k", 1), "bad %s", "thing")
        error_once(("k", 2), "bad %s", "other")
    assert [r.getMessage() for r in caplog.records] == ["bad thing", "bad other"]


def test_report_collision_names_both_tools(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="core.conscious.tools.names"):
        report_collision("home_x", "home.x", "home_x")
        report_collision("home_x", "home.x", "home_x")
    assert len(caplog.records) == 1
    assert "'home.x' and 'home_x' both map to wire name 'home_x'" in caplog.text
