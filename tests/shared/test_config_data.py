"""Tests for the data-root helpers in shared.config."""

from __future__ import annotations

from pathlib import Path

import pytest  # noqa: TC002

from shared import config


def test_data_root_defaults_to_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_DATA_DIR", raising=False)
    assert config.data_root() == (Path("data").resolve())


def test_data_root_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    assert config.data_root() == tmp_path.resolve()


def test_data_path_ensures_parent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    p = config.data_path("nested", "file.db")
    assert p == (tmp_path / "nested" / "file.db").resolve()
    assert p.parent.is_dir()  # parent created, file itself not


def test_data_mode_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_DATA_MODE", raising=False)
    assert config.data_mode() == "persistent"
    monkeypatch.setenv("ALFRED_DATA_MODE", "seed")
    assert config.data_mode() == "seed"
