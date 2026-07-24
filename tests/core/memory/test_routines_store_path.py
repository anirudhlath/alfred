"""RoutineStore defaults its directory to the data dir."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.memory import paths
from core.memory.routines.store import RoutineStore

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_routine_store_defaults_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    store = RoutineStore()
    assert store._dir == paths.routines_dir()
    assert str(tmp_path) in str(store._dir)
