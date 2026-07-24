"""stage_context() must include tracked+untracked files and exclude gitignored ones."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alfredctl import staging


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def fake_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for repo in ("alfred", "home-service"):
        root = tmp_path / repo
        root.mkdir()
        _git(root, "init", "-q")
        (root / ".gitignore").write_text(".env\nsecret.md\n")
        (root / "kept.py").write_text("x = 1\n")
        (root / ".env").write_text("SECRET=1\n")
        (root / "secret.md").write_text("personal\n")
        _git(root, "add", "-A")
        _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        (root / "untracked_new.py").write_text("y = 2\n")  # new file, not ignored
    monkeypatch.setattr(staging, "repo_root", lambda: tmp_path / "alfred")
    monkeypatch.setattr(staging, "workspace_root", lambda: tmp_path)
    return tmp_path


def test_stage_includes_tracked_and_untracked(fake_workspace: Path, tmp_path: Path) -> None:
    dest = staging.stage_context(tmp_path / "stage")
    assert (dest / "alfred" / "kept.py").is_file()
    assert (dest / "alfred" / "untracked_new.py").is_file()
    assert (dest / "home-service" / "kept.py").is_file()


def test_stage_excludes_gitignored(fake_workspace: Path, tmp_path: Path) -> None:
    dest = staging.stage_context(tmp_path / "stage")
    assert not (dest / "alfred" / ".env").exists()
    assert not (dest / "alfred" / "secret.md").exists()


def test_stage_missing_home_service_raises(
    fake_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    shutil.rmtree(fake_workspace / "home-service")
    with pytest.raises(FileNotFoundError, match="home-service"):
        staging.stage_context(tmp_path / "stage")


def test_build_stage_root_lives_under_home() -> None:
    # Apple `container`'s builder VM only shares $HOME — a context staged in the
    # system temp dir appears empty to the builder. Lock the location in.
    root = staging.build_stage_root()
    assert root.is_relative_to(Path.home())
    assert root.is_dir()
