"""Preference/profile dirs resolve under the data dir."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.channels.web_server import _get_prefs_dirs
from core.memory import paths

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_prefs_dirs_under_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    prefs, profile = _get_prefs_dirs()
    assert prefs == paths.preferences_dir()
    assert profile == paths.profile_dir()
