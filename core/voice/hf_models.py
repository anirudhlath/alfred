"""Shared Hugging Face Hub model downloader for the voice backends."""

from __future__ import annotations

from pathlib import Path


def ensure_model(repo_id: str, filename: str, revision: str) -> Path:
    """Download (cached) a model file from the HF Hub, pinned to ``revision``.

    Wraps ``huggingface_hub.hf_hub_download`` — the local cache
    (``~/.cache/huggingface/hub``), resume, and integrity are handled for us.
    Returns the local path to the file.
    """
    from huggingface_hub import hf_hub_download

    return Path(hf_hub_download(repo_id=repo_id, filename=filename, revision=revision))
