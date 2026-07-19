"""Tests for centralized memory paths + first-boot seeding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.memory import paths

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_paths_derive_from_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    assert paths.scratchpad_path() == (tmp_path / "scratchpad.md").resolve()
    assert paths.episodic_cold_path() == (tmp_path / "episodic_cold.db").resolve()
    assert paths.routines_dir() == (tmp_path / "routines").resolve()
    assert paths.routines_dir().is_dir()
    assert paths.preferences_dir().is_dir()
    assert paths.profile_dir().is_dir()
    assert paths.triggers_snapshot_dir().is_dir()


def test_seed_defaults_copies_templates_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Fake package template dir with one preference file.
    pkg = tmp_path / "pkg_prefs"
    pkg.mkdir()
    (pkg / "core.md").write_text("# seed", encoding="utf-8")
    monkeypatch.setattr(paths, "PKG_PREFERENCES", pkg)
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path / "data"))

    paths.seed_defaults()
    copied = paths.preferences_dir() / "core.md"
    assert copied.read_text(encoding="utf-8") == "# seed"


def test_seed_defaults_never_overwrites(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pkg = tmp_path / "pkg_prefs"
    pkg.mkdir()
    (pkg / "core.md").write_text("# template", encoding="utf-8")
    monkeypatch.setattr(paths, "PKG_PREFERENCES", pkg)
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path / "data"))

    target = paths.preferences_dir() / "core.md"
    target.write_text("# user edited", encoding="utf-8")
    paths.seed_defaults()
    assert target.read_text(encoding="utf-8") == "# user edited"  # untouched
