"""The EMBEDDING_BACKEND seam: one env var picks the provider every service builds."""

from __future__ import annotations

import dataclasses

import pytest

from core.memory.embedding_backend import _BACKENDS, build_embedding_provider
from shared.config import DEFAULT_EMBEDDING_BACKEND, AlfredConfig


def _config(**overrides: object) -> AlfredConfig:
    # AlfredConfig is a frozen dataclass, so replace() gives a config carrying the
    # declared defaults with no env reads — these tests stay independent of the
    # developer's shell.
    return dataclasses.replace(AlfredConfig(), **overrides)  # type: ignore[arg-type]


def test_openai_backend_builds_the_http_provider() -> None:
    """Every field the factory forwards is asserted here, or a mis-wiring is invisible.

    Deleting ``api_key=`` (or pointing ``host=`` at the wrong config field) leaves the
    provider's own tests green — it defaults the key to "" and never validates the host
    offline. The private attributes are this factory's contract with its one collaborator.
    """
    from core.memory.openai_embedding_provider import OpenAICompatEmbeddingProvider

    provider = build_embedding_provider(
        _config(
            embedding_backend="openai",
            embedding_host="http://embed:8001",
            embedding_api_key="sk-test",
            embedding_model="BAAI/bge-m3",
            embedding_dim=1024,
        )
    )
    assert isinstance(provider, OpenAICompatEmbeddingProvider)
    assert provider.model_name() == "BAAI/bge-m3"
    assert provider.dimension() == 1024
    assert provider._host == "http://embed:8001"
    assert provider._headers == {"Authorization": "Bearer sk-test"}


def test_openai_backend_sends_no_auth_header_without_a_key() -> None:
    # The common case: a vLLM started without --api-key. An empty bearer token is
    # worse than no header, so the blank key must not become one.
    provider = build_embedding_provider(_config(embedding_backend="openai"))
    assert provider._headers == {}  # type: ignore[attr-defined]


def test_default_backend_builds_sentence_transformers() -> None:
    from core.memory.embedding_provider import SentenceTransformerProvider

    provider = build_embedding_provider(_config(embedding_model="all-MiniLM-L6-v2"))
    assert isinstance(provider, SentenceTransformerProvider)
    assert provider.model_name() == "all-MiniLM-L6-v2"


def test_every_registered_backend_has_a_builder() -> None:
    """A registered name must build. This is the failure the module exists to prevent.

    With if/elif dispatch, a backend added to the accepted-names list without a branch
    fell through to sentence-transformers and silently loaded an in-process torch model
    — a wrong provider, no error. The registry makes that unrepresentable; this test
    keeps it that way, and proves each entry is callable rather than merely present.
    """
    from core.memory.embedding_provider import EmbeddingProvider

    assert set(_BACKENDS) == {DEFAULT_EMBEDDING_BACKEND, "openai"}
    for name in _BACKENDS:
        provider = build_embedding_provider(_config(embedding_backend=name))
        assert isinstance(provider, EmbeddingProvider)


def test_registry_tracks_the_configured_default() -> None:
    # Re-spelling "sentence_transformers" here would let the registry drift from
    # shared/config.py's default, making the default backend unbuildable.
    assert DEFAULT_EMBEDDING_BACKEND in _BACKENDS


def test_unknown_backend_fails_loudly() -> None:
    with pytest.raises(RuntimeError, match="Unknown EMBEDDING_BACKEND"):
        build_embedding_provider(_config(embedding_backend="banana"))


def test_unknown_backend_error_quotes_the_raw_value() -> None:
    """Reporting only the normalised value gives a string that isn't in the user's .env."""
    with pytest.raises(RuntimeError, match=r"'Sentence Transformers'"):
        build_embedding_provider(_config(embedding_backend="Sentence Transformers"))


def test_backend_name_is_normalised() -> None:
    """from_env normalises already; a hand-built config (tests, embedders) does not."""
    from core.memory.openai_embedding_provider import OpenAICompatEmbeddingProvider

    provider = build_embedding_provider(_config(embedding_backend=" OpenAI "))
    assert isinstance(provider, OpenAICompatEmbeddingProvider)


def test_blank_backend_falls_back_to_the_default() -> None:
    from core.memory.embedding_provider import SentenceTransformerProvider

    provider = build_embedding_provider(_config(embedding_backend="   "))
    assert isinstance(provider, SentenceTransformerProvider)
