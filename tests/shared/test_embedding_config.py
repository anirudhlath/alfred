"""Embedding model default is ungated and its dimension stays in sync."""

from __future__ import annotations

import pytest  # noqa: TC002

from shared import config


def test_default_model_is_ungated() -> None:
    # A fresh clone must embed with no HF token / license — no gated repo as default.
    assert not config.DEFAULT_EMBEDDING_MODEL.startswith("google/embeddinggemma")


def test_dim_lookup_matches_known_models() -> None:
    assert config.embedding_dim_for("sentence-transformers/all-MiniLM-L6-v2") == 384
    assert config.embedding_dim_for("sentence-transformers/all-mpnet-base-v2") == 768
    assert config.embedding_dim_for("google/embeddinggemma-300m") == 768


def test_dim_lookup_unknown_defaults_to_384() -> None:
    assert config.embedding_dim_for("some/unknown-model") == 384


def test_from_env_default_pairs_model_and_dim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_DIM", raising=False)
    cfg = config.AlfredConfig.from_env()
    assert cfg.embedding_model == config.DEFAULT_EMBEDDING_MODEL
    assert cfg.embedding_dim == config.embedding_dim_for(config.DEFAULT_EMBEDDING_MODEL)


def test_from_env_dim_tracks_known_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")
    monkeypatch.delenv("EMBEDDING_DIM", raising=False)
    cfg = config.AlfredConfig.from_env()
    assert cfg.embedding_dim == 768


def test_from_env_explicit_dim_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "custom/model")
    monkeypatch.setenv("EMBEDDING_DIM", "512")
    cfg = config.AlfredConfig.from_env()
    assert cfg.embedding_dim == 512
