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


def test_research_vault_defaults_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Telemetry writes under research_vault_path; unset RESEARCH_VAULT_PATH must
    # resolve inside ALFRED_DATA_DIR so a container/worktree never writes into the
    # tracked source tree.
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RESEARCH_VAULT_PATH", raising=False)
    cfg = config.AlfredConfig.from_env()
    assert Path(cfg.research_vault_path) == (tmp_path / "research").resolve()


def test_research_vault_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RESEARCH_VAULT_PATH", "/my/obsidian/vault")
    cfg = config.AlfredConfig.from_env()
    assert cfg.research_vault_path == "/my/obsidian/vault"


def test_models_root_defaults_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ALFRED_MODELS_DIR", raising=False)
    assert config.models_root() == (tmp_path / "models").resolve()
    assert config.models_root().is_dir()


def test_models_root_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_MODELS_DIR", str(tmp_path / "cache"))
    assert config.models_root() == (tmp_path / "cache").resolve()
