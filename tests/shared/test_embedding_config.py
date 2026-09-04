"""Embedding model default is ungated and its dimension stays in sync."""

from __future__ import annotations

import pytest

from shared import config


def test_default_model_is_ungated() -> None:
    # A fresh clone must embed with no HF token / license — no gated repo as default.
    assert not config.DEFAULT_EMBEDDING_MODEL.startswith("google/embeddinggemma")


def test_dim_lookup_matches_known_models() -> None:
    assert config.embedding_dim_for("sentence-transformers/all-MiniLM-L6-v2") == 384
    assert config.embedding_dim_for("sentence-transformers/all-mpnet-base-v2") == 768
    assert config.embedding_dim_for("google/embeddinggemma-300m") == 768
    assert config.embedding_dim_for("BAAI/bge-m3") == 1024


def test_dim_lookup_unknown_defaults_to_384() -> None:
    assert config.embedding_dim_for("some/unknown-model") == 384


def test_from_env_default_pairs_model_and_dim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_DIM", raising=False)
    cfg = config.AlfredConfig.from_env()
    assert cfg.embedding_model == config.DEFAULT_EMBEDDING_MODEL
    assert cfg.embedding_dim == config.embedding_dim_for(config.DEFAULT_EMBEDDING_MODEL)


def test_from_env_dim_tracks_known_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBEDDING_DIM", raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")
    assert config.AlfredConfig.from_env().embedding_dim == 768
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    assert config.AlfredConfig.from_env().embedding_dim == 1024


def test_from_env_explicit_dim_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "custom/model")
    monkeypatch.setenv("EMBEDDING_DIM", "512")
    cfg = config.AlfredConfig.from_env()
    assert cfg.embedding_dim == 512


def test_embedding_backend_defaults_to_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMBEDDING_BACKEND", raising=False)
    monkeypatch.delenv("EMBEDDING_HOST", raising=False)
    cfg = config.AlfredConfig.from_env()
    assert cfg.embedding_backend == "sentence_transformers"
    assert cfg.embedding_host == "http://localhost:8001"


def test_embedding_backend_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_BACKEND", "OpenAI")
    monkeypatch.setenv("EMBEDDING_HOST", "http://vllm:8001/")
    cfg = config.AlfredConfig.from_env()
    # Normalised: lowercased, and no trailing slash (the client appends /v1/...).
    assert cfg.embedding_backend == "openai"
    assert cfg.embedding_host == "http://vllm:8001"


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_embedding_backend_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # `.env` sets a bare `EMBEDDING_BACKEND=` to "", which satisfies os.getenv and
    # defeats its default — the fallback has to be on the value, not the lookup.
    monkeypatch.setenv("EMBEDDING_BACKEND", raw)
    assert config.AlfredConfig.from_env().embedding_backend == "sentence_transformers"


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_embedding_host_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # Same trap as the backend, but worse: "" would build a schemeless
    # "/v1/embeddings" and httpx fails with an opaque UnsupportedProtocol.
    monkeypatch.setenv("EMBEDDING_HOST", raw)
    assert config.AlfredConfig.from_env().embedding_host == "http://localhost:8001"


@pytest.mark.parametrize(
    "raw",
    [
        "http://vllm:8001",
        "http://vllm:8001/",
        "http://vllm:8001/v1",
        "http://vllm:8001/v1/",
        "  http://vllm:8001/v1  ",
    ],
)
def test_embedding_host_normalises_to_a_bare_origin(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # The client appends /v1/embeddings, and "http://host:8001/v1" is how vLLM and
    # the OpenAI docs print a base URL — keeping it would request /v1/v1/embeddings.
    monkeypatch.setenv("EMBEDDING_HOST", raw)
    assert config.AlfredConfig.from_env().embedding_host == "http://vllm:8001"


def test_embedding_host_keeps_a_path_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only a trailing /v1 is the docs' base-URL idiom; a proxy path must survive.
    monkeypatch.setenv("EMBEDDING_HOST", "http://gateway/openai/v1")
    assert config.AlfredConfig.from_env().embedding_host == "http://gateway/openai"


def test_embedding_api_key_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # No key is the common case: a vLLM started without --api-key rejects nothing,
    # and sending an empty bearer token to it would be worse than sending none.
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    assert config.AlfredConfig.from_env().embedding_api_key == ""


def test_embedding_api_key_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # A server started with --api-key (or real OpenAI) is otherwise unreachable
    # through the factory, which passes no pre-configured client.
    monkeypatch.setenv("EMBEDDING_API_KEY", "sk-secret")
    assert config.AlfredConfig.from_env().embedding_api_key == "sk-secret"


def test_unknown_backend_is_rejected_at_config_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """One typo, one failure — not four services diverging.

    Before validation moved here, a bad EMBEDDING_BACKEND reached the factory in each
    process and produced three different outcomes: conscious and the librarian caught
    it and ran on with memory silently disabled, the admin API cached a permanent
    failure, and the ingestor died with a traceback.
    """
    monkeypatch.setenv("EMBEDDING_BACKEND", "banana")
    with pytest.raises(RuntimeError, match="Unknown EMBEDDING_BACKEND"):
        config.AlfredConfig.from_env()


def test_backend_rejection_quotes_the_raw_value(monkeypatch: pytest.MonkeyPatch) -> None:
    # The normalised spelling appears nowhere in the user's .env.
    monkeypatch.setenv("EMBEDDING_BACKEND", "Sentence Transformers")
    with pytest.raises(RuntimeError, match=r"'Sentence Transformers'"):
        config.AlfredConfig.from_env()


@pytest.mark.parametrize("raw", ["openai", "OpenAI", "  openai  "])
def test_known_backends_are_accepted_and_normalised(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("EMBEDDING_BACKEND", raw)
    assert config.AlfredConfig.from_env().embedding_backend == "openai"


def test_blank_backend_means_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # `EMBEDDING_BACKEND=` in .env sets the key to "", defeating os.getenv's default.
    monkeypatch.setenv("EMBEDDING_BACKEND", "")
    assert config.AlfredConfig.from_env().embedding_backend == config.DEFAULT_EMBEDDING_BACKEND


def test_embedding_timeout_defaults_and_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """30s is a hang detector, not a throughput limit — 2048 bge-m3 inputs took 1.72s.

    It matters because a server that accepts the connection and then stalls (a vLLM
    mid-model-load) is not covered by the tighter connect budget, and involuntary
    recall embeds inline in the reply path.
    """
    monkeypatch.delenv("EMBEDDING_TIMEOUT_SECONDS", raising=False)
    assert config.AlfredConfig.from_env().embedding_timeout_seconds == 30.0

    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", "5")
    assert config.AlfredConfig.from_env().embedding_timeout_seconds == 5.0

    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", "")
    assert config.AlfredConfig.from_env().embedding_timeout_seconds == 30.0


def test_non_numeric_timeout_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """`could not convert string to float: 'abc'` names neither the var nor the file."""
    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", "abc")
    with pytest.raises(RuntimeError, match="EMBEDDING_TIMEOUT_SECONDS must be a number"):
        config.AlfredConfig.from_env()


@pytest.mark.parametrize("raw", ["0", "-1", "-0.5"])
def test_non_positive_timeout_is_rejected(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """httpx reads these as "give up immediately", never as the "wait longer" intended."""
    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", raw)
    with pytest.raises(RuntimeError, match="EMBEDDING_TIMEOUT_SECONDS must be greater than 0"):
        config.AlfredConfig.from_env()


def test_timeout_error_quotes_the_raw_value(monkeypatch: pytest.MonkeyPatch) -> None:
    # The operator greps their .env for what they typed, not for a parsed float.
    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", " 0 ")
    with pytest.raises(RuntimeError, match=r"'0'"):
        config.AlfredConfig.from_env()
