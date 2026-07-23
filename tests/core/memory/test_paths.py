"""Tests for centralized memory paths + first-boot seeding."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.memory import paths
from core.memory.paths import seed_defaults

if TYPE_CHECKING:
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


def test_seed_promotes_example_templates_to_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    seed_defaults()
    prefs = tmp_path / "preferences"
    # every packaged .example template must land as an ACTIVE top-level file
    pkg_examples = Path(paths.__file__).parent / "preferences" / ".example"
    for tpl in pkg_examples.glob("*.md"):
        assert (prefs / tpl.name).is_file(), tpl.name
    # and no inert .example/ copy in the data dir
    assert not (prefs / ".example").exists()

    # profile templates promote the same way, if the package ships any.
    profile = tmp_path / "profile"
    pkg_profile_examples = Path(paths.__file__).parent / "profile" / ".example"
    if pkg_profile_examples.is_dir():
        for tpl in pkg_profile_examples.glob("*.md"):
            assert (profile / tpl.name).is_file(), tpl.name
        assert not (profile / ".example").exists()


def test_seed_never_overwrites_existing_active_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    prefs = tmp_path / "preferences"
    prefs.mkdir(parents=True)
    pkg_examples = Path(paths.__file__).parent / "preferences" / ".example"
    name = next(pkg_examples.glob("*.md")).name
    (prefs / name).write_text("user-owned content")
    seed_defaults()
    assert (prefs / name).read_text() == "user-owned content"


def test_seed_real_top_level_file_wins_over_example_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real (gitignored, dev-owned) top-level package file must ALWAYS win over a
    `.example/` template of the same name, deterministically — never dependent on
    filesystem/glob traversal order. Real files seed as Part 1 always has; templates
    only fill the gap when no real file exists.
    """
    # Package-shaped preferences root: a real top-level file AND a `.example` template
    # with the same name (mirrors a dev worktree where both can genuinely coexist).
    pkg = tmp_path / "pkg_prefs"
    pkg.mkdir()
    (pkg / "name.md").write_text("real", encoding="utf-8")
    (pkg / ".example").mkdir()
    (pkg / ".example" / "name.md").write_text("template", encoding="utf-8")
    monkeypatch.setattr(paths, "PKG_PREFERENCES", pkg)
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path / "data"))

    seed_defaults()
    seeded = paths.preferences_dir() / "name.md"
    assert seeded.read_text(encoding="utf-8") == "real"
