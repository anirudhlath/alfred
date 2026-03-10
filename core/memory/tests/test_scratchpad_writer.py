"""Tests for the scratchpad async writer."""

import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_scratchpad_writer_drains_queue() -> None:
    from unittest.mock import AsyncMock

    from core.memory.scratchpad_writer import ScratchpadWriter

    mock_redis = AsyncMock()
    # Simulate batch LPOP returning a list of entries
    mock_redis.lpop = AsyncMock(
        return_value=[
            b"2026-03-10T14:00:00Z [reflex] TV turned on in living room",
            b"2026-03-10T14:00:01Z [reflex] Dimmed lights to 20%",
        ]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        scratchpad_path = os.path.join(tmpdir, "scratchpad.md")
        with open(scratchpad_path, "w") as f:
            f.write("---\nlast_drain: null\n---\n# Scratchpad\n")

        writer = ScratchpadWriter(
            redis=mock_redis,
            queue_key="alfred:scratchpad:queue",
            scratchpad_path=scratchpad_path,
        )
        drained = await writer.drain_once()

        assert drained == 2
        with open(scratchpad_path) as f:
            content = f.read()
        assert "TV turned on" in content
        assert "Dimmed lights" in content


@pytest.mark.asyncio
async def test_scratchpad_writer_empty_queue() -> None:
    from unittest.mock import AsyncMock

    from core.memory.scratchpad_writer import ScratchpadWriter

    mock_redis = AsyncMock()
    mock_redis.lpop = AsyncMock(return_value=None)

    with tempfile.TemporaryDirectory() as tmpdir:
        scratchpad_path = os.path.join(tmpdir, "scratchpad.md")
        with open(scratchpad_path, "w") as f:
            f.write("# Scratchpad\n")

        writer = ScratchpadWriter(
            redis=mock_redis,
            queue_key="alfred:scratchpad:queue",
            scratchpad_path=scratchpad_path,
        )
        drained = await writer.drain_once()

        assert drained == 0
