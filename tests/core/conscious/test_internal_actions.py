"""run_librarian internal action dispatch."""

from unittest.mock import AsyncMock

import pytest

from core.conscious.__main__ import _INTERNAL_HANDLERS


@pytest.mark.asyncio
async def test_run_librarian_handler_calls_consolidate() -> None:
    librarian = AsyncMock()
    librarian.consolidate = AsyncMock(return_value={"entries_processed": 0})

    async def _run() -> None:
        await librarian.consolidate()

    _INTERNAL_HANDLERS["run_librarian"] = _run
    try:
        await _INTERNAL_HANDLERS["run_librarian"]()
        librarian.consolidate.assert_awaited_once()
    finally:
        _INTERNAL_HANDLERS.pop("run_librarian", None)
