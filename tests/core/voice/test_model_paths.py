"""Model caches must resolve under models_root(), never the package tree or cwd.

Piper and Kokoro voices download via ``core.voice.hf_models.ensure_model`` into the
HF hub cache (``HF_HOME`` — mounted at /models/hf in the container), so only the
speechbrain ECAPA cache still routes through ``models_root()`` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest  # noqa: TC002

if TYPE_CHECKING:
    from pathlib import Path


def test_speaker_id_model_dir_under_models_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_MODELS_DIR", str(tmp_path))
    from core.voice.speaker_id import _model_dir

    assert _model_dir() == (tmp_path / "spkrec-ecapa-voxceleb").resolve()
