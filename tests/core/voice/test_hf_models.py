"""Tests for the shared HF Hub model downloader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import core.voice.hf_models as hf

if TYPE_CHECKING:
    from pathlib import Path


def test_ensure_model_delegates_to_hf(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    calls: dict[str, str] = {}

    def fake_download(repo_id: str, filename: str, revision: str) -> str:
        calls.update(repo_id=repo_id, filename=filename, revision=revision)
        dest = tmp_path / filename.replace("/", "_")
        dest.write_bytes(b"x")
        return str(dest)

    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_download)
    out = hf.ensure_model("some/repo", "a/b.onnx", "deadbeef")
    assert out == tmp_path / "a_b.onnx"
    assert calls == {"repo_id": "some/repo", "filename": "a/b.onnx", "revision": "deadbeef"}
