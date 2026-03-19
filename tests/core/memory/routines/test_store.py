"""Tests for RoutineStore."""

from __future__ import annotations

from pathlib import Path

from core.memory.routines.store import RoutineStore
from core.memory.schemas import RoutineSpec, RoutineStep


def test_save_and_load(tmp_path: Path) -> None:
    store = RoutineStore(routines_dir=str(tmp_path))
    routine = RoutineSpec(
        name="evening_movie",
        trigger_pattern="every evening around 8pm",
        steps=[RoutineStep(description="Dim living room to 30%")],
        confidence=0.7,
        learned_from=["ep-1", "ep-2"],
        state="candidate",
    )
    store.save(routine)
    loaded = store.get("evening_movie")
    assert loaded is not None
    assert loaded.confidence == 0.7


def test_list_all(tmp_path: Path) -> None:
    store = RoutineStore(routines_dir=str(tmp_path))
    for i in range(3):
        store.save(
            RoutineSpec(
                name=f"routine_{i}",
                trigger_pattern=f"pattern {i}",
                steps=[RoutineStep(description=f"step {i}")],
                confidence=0.5,
                learned_from=[],
                state="candidate",
            )
        )
    all_routines = store.list_all()
    assert len(all_routines) == 3


def test_list_by_state(tmp_path: Path) -> None:
    store = RoutineStore(routines_dir=str(tmp_path))
    store.save(
        RoutineSpec(
            name="active_one",
            trigger_pattern="p",
            steps=[],
            confidence=0.9,
            learned_from=[],
            state="active",
        )
    )
    store.save(
        RoutineSpec(
            name="candidate_one",
            trigger_pattern="p",
            steps=[],
            confidence=0.5,
            learned_from=[],
            state="candidate",
        )
    )
    active = store.list_by_state("active")
    assert len(active) == 1
    assert active[0].name == "active_one"


def test_delete(tmp_path: Path) -> None:
    store = RoutineStore(routines_dir=str(tmp_path))
    store.save(
        RoutineSpec(
            name="to_delete",
            trigger_pattern="p",
            steps=[],
            confidence=0.5,
            learned_from=[],
            state="candidate",
        )
    )
    store.delete("to_delete")
    assert store.get("to_delete") is None
