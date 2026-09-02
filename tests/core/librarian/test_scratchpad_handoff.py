"""The Librarian must actually receive what the Conscious Engine wrote down.

Regression: ScratchpadWriter (every 5s) and Librarian (hourly) both consumed
``alfred:scratchpad:queue``. The writer always won, so every consolidation cycle
logged "Scratchpad empty — nothing to consolidate" and nothing a user ever said
reached episodic, semantic or procedural memory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.librarian.consolidator import Librarian
from core.memory.scratchpad_writer import ScratchpadWriter
from shared.streams import LIBRARIAN_QUEUE, SCRATCHPAD_QUEUE

if TYPE_CHECKING:
    from pathlib import Path


class FakeListRedis:
    """Minimal real-behaviour Redis list: the ops both consumers actually use."""

    def __init__(self) -> None:
        self.lists: dict[str, list[bytes]] = {}

    async def lpush(self, key: str, *values: str) -> int:
        bucket = self.lists.setdefault(key, [])
        for v in values:
            bucket.insert(0, v.encode())
        return len(bucket)

    async def rpush(self, key: str, *values: str | bytes) -> int:
        bucket = self.lists.setdefault(key, [])
        bucket.extend(v if isinstance(v, bytes) else v.encode() for v in values)
        return len(bucket)

    async def lpop(self, key: str, count: int | None = None) -> list[bytes] | bytes | None:
        bucket = self.lists.get(key, [])
        if not bucket:
            return None
        if count is None:
            return bucket.pop(0)
        taken, self.lists[key] = bucket[:count], bucket[count:]
        return taken

    async def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        bucket = self.lists.get(key, [])
        return bucket[start:] if end == -1 else bucket[start : end + 1]

    async def rename(self, src: str, dst: str) -> None:
        if src not in self.lists:
            raise KeyError(src)  # Redis: RENAME on a missing key is an error
        self.lists[dst] = self.lists.pop(src)

    async def delete(self, key: str) -> int:
        return 1 if self.lists.pop(key, None) is not None else 0


def _librarian(redis: object) -> Librarian:
    context_index = AsyncMock()
    context_index.reindex_semantic_files = AsyncMock()
    return Librarian(  # type: ignore[arg-type]
        redis=redis,
        episodic_memory=AsyncMock(),
        routine_store=MagicMock(),
        significance_scorer=AsyncMock(),
        context_index=context_index,
    )


@pytest.mark.asyncio
async def test_writer_forwards_drained_entries_to_the_librarian(tmp_path: Path) -> None:
    redis = FakeListRedis()
    await redis.lpush(SCRATCHPAD_QUEUE, "2026-09-02T10:00:00Z [conscious] user='hello' → 5 chars")

    writer = ScratchpadWriter(redis=redis, scratchpad_path=str(tmp_path / "scratchpad.md"))  # type: ignore[arg-type]
    assert await writer.drain_once() == 1

    assert await redis.lrange(LIBRARIAN_QUEUE, 0, -1) == [
        b"2026-09-02T10:00:00Z [conscious] user='hello' \xe2\x86\x92 5 chars"
    ]


@pytest.mark.asyncio
async def test_entries_survive_the_writer_and_reach_consolidation(tmp_path: Path) -> None:
    """The end-to-end race: writer drains first, Librarian must still see everything."""
    redis = FakeListRedis()
    entries = [f"2026-09-02T10:0{i}:00Z [conscious] user='turn on the lights'" for i in range(3)]
    for entry in entries:
        await redis.lpush(SCRATCHPAD_QUEUE, entry)

    writer = ScratchpadWriter(redis=redis, scratchpad_path=str(tmp_path / "scratchpad.md"))  # type: ignore[arg-type]
    await writer.drain_once()

    drained = await _librarian(redis)._drain_scratchpad()
    assert sorted(drained) == sorted(entries)


@pytest.mark.asyncio
async def test_the_durable_scratchpad_file_is_still_written(tmp_path: Path) -> None:
    """Forwarding must not cost the human-readable log the admin UI reads."""
    redis = FakeListRedis()
    await redis.lpush(SCRATCHPAD_QUEUE, "2026-09-02T10:00:00Z [conscious] user='hi'")

    path = tmp_path / "scratchpad.md"
    await ScratchpadWriter(redis=redis, scratchpad_path=str(path)).drain_once()  # type: ignore[arg-type]

    assert "user='hi'" in path.read_text(encoding="utf-8")
