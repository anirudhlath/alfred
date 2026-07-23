"""Model caches must resolve under models_root(), never the package tree or cwd."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest  # noqa: TC002

if TYPE_CHECKING:
    from pathlib import Path


def test_piper_default_model_dir_under_models_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_MODELS_DIR", str(tmp_path))
    from core.voice.tts import _default_model_dir

    assert _default_model_dir() == (tmp_path / "piper").resolve()


def test_speaker_id_model_dir_under_models_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_MODELS_DIR", str(tmp_path))
    from core.voice.speaker_id import _model_dir

    assert _model_dir() == (tmp_path / "spkrec-ecapa-voxceleb").resolve()
