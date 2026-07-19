"""The scratchpad writer and cold store default to the data dir, not the package."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.memory import paths
from core.memory.scratchpad_writer import ScratchpadWriter

if TYPE_CHECKING:
    import pytest


def test_scratchpad_writer_defaults_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    writer = ScratchpadWriter(redis=None)
    assert Path(writer.scratchpad_path) == paths.scratchpad_path()
    assert str(tmp_path) in writer.scratchpad_path
