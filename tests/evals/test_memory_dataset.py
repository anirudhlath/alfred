"""The simulated house: deterministic, well-formed, and honest about its probes."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from evals.memory.dataset import NOISE, SIM_START, Category, build_dataset
from evals.memory.scenario import DETAIL_OBSERVATIONS, RECALLED_MEMORIES, SIGNIFICANT_EVENTS


def test_same_seed_same_timeline() -> None:
    assert build_dataset(seed=3, days=5).model_dump() == build_dataset(seed=3, days=5).model_dump()


def test_different_seed_different_timeline() -> None:
    a = build_dataset(seed=3, days=5)
    b = build_dataset(seed=4, days=5)
    assert [m.at for m in a.memories] != [m.at for m in b.memories]


def test_memories_are_unique_and_in_write_order() -> None:
    dataset = build_dataset(days=10)
    ids = [m.id for m in dataset.memories]
    assert len(ids) == len(set(ids))
    assert [m.at for m in dataset.memories] == sorted(m.at for m in dataset.memories)


def test_every_memory_has_exactly_one_write_path() -> None:
    for memory in build_dataset(days=10).memories:
        if memory.observation is None:
            assert memory.summary and memory.source
        else:
            # The ingestor keys the entry on the observation id — they must agree.
            assert memory.observation.observation_id == memory.id
            assert not memory.summary


def test_full_run_is_a_busy_house() -> None:
    dataset = build_dataset()
    counts = Counter(m.category for m in dataset.memories)
    per_day = (counts[Category.ROUTINE] + counts[Category.REFLEX]) / dataset.days
    assert 180 <= per_day <= 260  # the ingestor's own estimate is ~250/day
    assert counts[Category.SIGNIFICANT] == len(SIGNIFICANT_EVENTS)
    assert counts[Category.RECALLED] == len(RECALLED_MEMORIES)
    assert counts[Category.DETAIL] == len(DETAIL_OBSERVATIONS)
    assert len(dataset.probes) >= 30


def test_shorter_runs_drop_later_content() -> None:
    dataset = build_dataset(days=10)
    end = SIM_START + timedelta(days=10)
    assert all(m.at < end for m in dataset.memories)
    targets = {m.id for m in dataset.memories}
    assert all(p.target_id in targets for p in dataset.probes)
    assert all(r.at < end for r in dataset.retrievals)


def test_probes_point_at_distinct_targets() -> None:
    dataset = build_dataset()
    by_id = {m.id: m for m in dataset.memories}
    targets = [p.target_id for p in dataset.probes]
    assert len(targets) == len(set(targets))
    for probe in dataset.probes:
        assert by_id[probe.target_id].category == probe.category
        assert by_id[probe.target_id].category not in NOISE


def test_observation_targets_never_repeat_in_the_noise() -> None:
    """A detail target is only distinctive if no routine event shares its transition."""
    dataset = build_dataset()

    def key(memory_id: str) -> tuple[object, ...]:
        observation = next(m for m in dataset.memories if m.id == memory_id).observation
        assert observation is not None
        event = observation.trigger_event
        return (
            event["entity_id"],
            event["old_state"],
            event["new_state"],
            str(event["attributes"]),
        )

    targets = {p.target_id for p in dataset.probes}
    observed_targets = {
        key(m.id) for m in dataset.memories if m.id in targets and m.observation is not None
    }
    noise = {
        key(m.id)
        for m in dataset.memories
        if m.observation is not None and m.observation.action is None and m.id not in targets
    }
    assert observed_targets
    assert not observed_targets & noise


def test_retrievals_only_follow_recalled_memories() -> None:
    dataset = build_dataset()
    by_id = {m.id: m for m in dataset.memories}
    assert dataset.retrievals
    for retrieval in dataset.retrievals:
        target = by_id[retrieval.target_id]
        assert target.category == Category.RECALLED
        assert retrieval.at > target.at


def test_index_content_is_seeded() -> None:
    dataset = build_dataset(days=1)
    assert dataset.semantic_files
    assert {r.state for r in dataset.routines} <= {"active", "candidate"}
