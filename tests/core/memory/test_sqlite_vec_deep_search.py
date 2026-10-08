"""SqliteVecStore searches deeper than vec0's KNN ceiling.

Deliberate recall reads a store deeper, its depth doubling, until it can prove nothing
it has not read could still place (issue #311), so the cold store must answer a search
for more than vec0's 4096 neighbours instead of failing it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.memory.sqlite_vec_store import _VEC_KNN_MAX_K, SqliteVecStore, _pack

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def store() -> AsyncIterator[SqliteVecStore]:
    """SqliteVecStore with an in-memory DB and sqlite-vec loaded."""
    s = SqliteVecStore(db_path=":memory:", dim=4)
    await s._ensure_schema()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_search_deeper_than_vec0s_knn_ceiling_ranks_every_vector(
    store: SqliteVecStore,
) -> None:
    """vec0 refuses a KNN query for more than 4096 neighbours, and the store used to
    answer that refusal with an empty list — which deliberate recall, reading deeper
    for its filters, would take for an archive with nothing more in it."""
    if not store._vec_ready:
        pytest.skip("sqlite-vec extension unavailable")
    count = _VEC_KNN_MAX_K + 2
    db = await store._get_db()
    # Straight to the tables: 4098 add() calls would commit 4098 times.
    await db.executemany(
        "INSERT INTO episodic_entries(rowid, id, timestamp, source, summary, entities, valence)"
        " VALUES (?, ?, 0, 'test', ?, '', 'neutral')",
        [(i + 1, f"ep-{i}", f"entry {i}") for i in range(count)],
    )
    for table in ("vec_episodic_content", "vec_episodic_semantic"):
        await db.executemany(
            f"INSERT INTO {table}(rowid, embedding) VALUES (?, ?)",
            # Ever further from the query as i grows, so the ranking is unambiguous.
            [(i + 1, _pack([1.0, i / count, 0.0, 0.0])) for i in range(count)],
        )
    await db.commit()
    query = [1.0, 0.0, 0.0, 0.0]

    deep = await store.search(query, limit=count)
    knn = await store.search(query, limit=_VEC_KNN_MAX_K)

    assert [r.id for r in deep] == [f"ep-{i}" for i in range(count)]
    assert [(r.id, r.score) for r in deep[:_VEC_KNN_MAX_K]] == [(r.id, r.score) for r in knn]
