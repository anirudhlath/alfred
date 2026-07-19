"""runner.__main__.build_services respects ALFRED_MANAGE_INFRA."""

from __future__ import annotations

from typing import TYPE_CHECKING

from runner.__main__ import build_services

if TYPE_CHECKING:
    import pytest


def test_core_only_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_MANAGE_INFRA", raising=False)
    names = {s.name for s in build_services()}
    assert names == {"bridge", "reflex", "triggers", "conscious", "channels", "memory-ingestor"}


def test_infra_added_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_MANAGE_INFRA", "1")
    names = {s.name for s in build_services()}
    assert {"redis", "mosquitto", "home-service"}.issubset(names)
    # redis/mosquitto are native-command services with readiness checks:
    by_name = {s.name: s for s in build_services()}
    assert by_name["redis"].command is not None
    assert by_name["redis"].ready_check is not None
