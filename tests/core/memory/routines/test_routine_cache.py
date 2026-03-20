"""Tests for RoutineStore in-memory caching."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.memory.routines.store import RoutineStore
from core.memory.schemas import RoutineSpec, RoutineStep

if TYPE_CHECKING:
    from pathlib import Path


def _make_routine(name: str, state: str = "active") -> RoutineSpec:
    """Helper to build a valid RoutineSpec."""
    return RoutineSpec(
        name=name,
        trigger_pattern="weekdays 07:30",
        steps=[RoutineStep(description="Turn on bedroom lights", action=None)],
        confidence=0.8,
        learned_from=["episode-1"],
        state=state,
    )


@pytest.fixture()
def store(tmp_path: Path) -> RoutineStore:
    return RoutineStore(routines_dir=str(tmp_path))


def test_list_all_caches_after_first_call(store: RoutineStore, tmp_path: Path) -> None:
    """Second list_all() should return cached result without re-globbing."""
    store.save(_make_routine("morning_lights"))

    store.list_all()
    # Delete file — but cache should persist
    (tmp_path / "morning_lights.yaml").unlink()
    result2 = store.list_all()
    assert len(result2) == 1  # still cached
    assert result2[0].name == "morning_lights"


def test_save_invalidates_cache(store: RoutineStore) -> None:
    """Saving a new routine should invalidate the cache."""
    store.save(_make_routine("r1"))
    assert len(store.list_all()) == 1

    store.save(_make_routine("r2"))
    assert len(store.list_all()) == 2  # cache invalidated, re-read
