"""The one-off that brings stored observations into slice 1's shape (issue #265)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from bus.schemas.events import ReflexObservation
from core.memory.ingestor import _build_observation_summary, local_stamp
from core.memory.migrate_observations import describe, migrate
from core.memory.sqlite_vec_store import SqliteVecStore
from core.memory.vector_store import ContextMetadata

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LA = ZoneInfo("America/Los_Angeles")
AT = datetime(2026, 10, 4, 0, 39, tzinfo=UTC)
STAMP = "Sat 2026-10-03 17:39"


def _axis(i: int) -> list[float]:
    return [1.0 if j == i else 0.0 for j in range(4)]


class _Embedder:
    """Every text lands on one axis, away from the vectors entries are added with."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(texts)
        return [_axis(2) for _ in texts]


@pytest.fixture
async def store() -> AsyncIterator[SqliteVecStore]:
    s = SqliteVecStore(db_path=":memory:", dim=4)
    await s._ensure_schema()
    yield s
    await s.close()


async def _add(
    store: SqliteVecStore,
    id_: str,
    content: str,
    *,
    timestamp: float = AT.timestamp(),
    source: str = "observation",
) -> None:
    meta = ContextMetadata(
        type="episodic",
        source=source,
        entities="",
        timestamp=timestamp,
        significance=0.1,
        retrieval_count=0,
    )
    await store.add(id_, content, "key", _axis(0), _axis(1), meta)


async def _contents(store: SqliteVecStore) -> dict[str, str]:
    return {r.id: r.content for r in await store.select({})}


async def test_availability_blips_are_deleted(store: SqliteVecStore) -> None:
    await _add(store, "drop", "[observation] light.a: on → unavailable")
    await _add(store, "back", "[observation] light.a: unavailable → on")
    await _add(
        store, "attrs", "[observation] media_player.mac: idle → unavailable (friendly_name=Mac)"
    )
    # The ingestor rendered a missing state as "unknown" too: an entity re-added at restart.
    await _add(store, "readded", "[observation] light.a: unknown → off")
    await _add(store, "real", "[observation] light.a: off → on")

    plan = await migrate(store, _Embedder(), LA, apply=True)

    assert sorted(r.id for r in plan.blips) == ["attrs", "back", "drop", "readded"]
    assert list(await _contents(store)) == ["real"]


async def test_a_real_change_reads_as_the_ingestor_writes_it_today(store: SqliteVecStore) -> None:
    obs = ReflexObservation(
        source="reflex-engine",
        origin="state_change",
        trigger_event={
            "entity_id": "light.a",
            "old_state": "off",
            "new_state": "on",
            "attributes": {"friendly_name": "Lamp"},
            "timestamp": AT.isoformat(),
        },
    )
    await _add(store, "real", "[observation] light.a: off → on (friendly_name=Lamp)")

    await migrate(store, _Embedder(), LA, apply=True)

    today = _build_observation_summary(obs, local_stamp(AT, LA))
    assert await _contents(store) == {"real": today}
    assert STAMP in today


async def test_the_new_text_gets_a_new_vector_and_keeps_its_key(store: SqliteVecStore) -> None:
    await _add(store, "real", "[observation] light.a: off → on")

    await migrate(store, _Embedder(), LA, apply=True)

    assert [r.id for r in await store.search(_axis(2), limit=5, min_similarity=0.9)] == ["real"]
    assert await store.search(_axis(0), limit=5, min_similarity=0.9) == []
    [by_key] = await store.search(_axis(1), limit=5, min_similarity=0.9)
    assert (by_key.id, by_key.semantic_key) == ("real", "key")


async def test_a_reflex_action_is_stamped_under_its_own_tag(store: SqliteVecStore) -> None:
    await _add(store, "act", "[reflex:state_change] home.turn_on(entity_id=light.a) → success")

    await migrate(store, _Embedder(), LA, apply=True)

    assert await _contents(store) == {
        "act": f"[reflex:state_change] {STAMP} — home.turn_on(entity_id=light.a) → success"
    }


async def test_other_memories_are_left_alone(store: SqliteVecStore) -> None:
    others = {
        "chat": "user='Test' → 41 chars (actions=none, tokens=17919+15)",
        "summary": "Desk light went unavailable twice, then came back on.",
        "fired": "Leave work reminder (type=time) fired",
    }
    for id_, content in others.items():
        await _add(store, id_, content, source="conscious")
    embedder = _Embedder()

    plan = await migrate(store, embedder, LA, apply=True)

    assert await _contents(store) == others
    assert (plan.blips, plan.rewrites, embedder.batches) == ([], [], [])


async def test_an_entry_with_no_time_is_left_alone_and_reported(store: SqliteVecStore) -> None:
    await _add(store, "undated", "[observation] light.a: off → on", timestamp=0.0)

    plan = await migrate(store, _Embedder(), LA, apply=True)

    assert [r.id for r in plan.undated] == ["undated"]
    assert await _contents(store) == {"undated": "[observation] light.a: off → on"}


async def test_a_second_run_changes_nothing(store: SqliteVecStore) -> None:
    await _add(store, "drop", "[observation] light.a: on → unavailable")
    await _add(store, "real", "[observation] light.a: off → on")
    await migrate(store, _Embedder(), LA, apply=True)
    after_first = await _contents(store)
    embedder = _Embedder()

    plan = await migrate(store, embedder, LA, apply=True)

    assert (plan.blips, plan.rewrites, embedder.batches) == ([], [], [])
    assert await _contents(store) == after_first


async def test_a_dry_run_reports_the_plan_and_writes_nothing(store: SqliteVecStore) -> None:
    await _add(store, "drop", "[observation] light.a: on → unavailable")
    await _add(store, "real", "[observation] light.a: off → on")
    before = await _contents(store)
    embedder = _Embedder()

    plan = await migrate(store, embedder, LA, apply=False)

    assert [r.id for r in plan.blips] == ["drop"]
    assert [(w.id, w.after) for w in plan.rewrites] == [
        ("real", f"[observation] {STAMP} — light.a: off → on")
    ]
    assert await _contents(store) == before
    assert embedder.batches == []


async def test_rewrites_are_embedded_in_batches(store: SqliteVecStore) -> None:
    for i in range(3):
        await _add(store, f"m{i}", f"[observation] light.a{i}: off → on")
    embedder = _Embedder()

    await migrate(store, embedder, LA, apply=True, batch_size=2)

    assert [len(batch) for batch in embedder.batches] == [2, 1]


async def test_an_entry_gone_before_its_rewrite_is_counted(
    store: SqliteVecStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decay pass running alongside can move an entry to cold between select and write."""
    await _add(store, "real", "[observation] light.a: off → on")
    monkeypatch.setattr(store, "replace_content", AsyncMock(return_value=False))

    plan = await migrate(store, _Embedder(), LA, apply=True)

    assert plan.gone == 1


async def test_describe_counts_each_kind_and_shows_a_rewrite(store: SqliteVecStore) -> None:
    await _add(store, "drop", "[observation] light.a: on → unavailable")
    await _add(store, "real", "[observation] light.a: off → on")
    await _add(store, "undated", "[observation] light.b: off → on", timestamp=0.0)
    plan = await migrate(store, _Embedder(), LA, apply=False)

    text = describe("cold", plan)

    assert "cold: 1 blips to delete, 1 to stamp, 1 without a time" in text
    assert f"[observation] {STAMP} — light.a: off → on" in text
