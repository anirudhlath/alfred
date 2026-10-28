"""Reflex's legacy OpenAI-compatible host resolves through a fallback chain, not raw getenv."""

from __future__ import annotations

import pytest  # noqa: TC002

from shared.config import AlfredConfig


def test_blank_openai_compat_host_falls_back_to_lmstudio(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` ships the key blank, and "" is not absent.

    Without the guard the server's address is a schemeless "" and httpx raises
    UnsupportedProtocol — the same failure ``normalize_embedding_host`` exists to prevent
    on the memory side.
    """
    monkeypatch.setenv("OPENAI_COMPAT_HOST", "")
    monkeypatch.setenv("LMSTUDIO_HOST", "http://lmstudio:1234")
    assert AlfredConfig.from_env().openai_compat_host == "http://lmstudio:1234"


def test_blank_lmstudio_host_falls_back_to_its_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPAT_HOST", "")
    monkeypatch.setenv("LMSTUDIO_HOST", "")
    cfg = AlfredConfig.from_env()
    assert cfg.lmstudio_host == "http://localhost:1234"
    assert cfg.openai_compat_host == "http://localhost:1234"


def test_explicit_openai_compat_host_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPAT_HOST", " http://vllm:8000 ")
    monkeypatch.setenv("LMSTUDIO_HOST", "http://lmstudio:1234")
    assert AlfredConfig.from_env().openai_compat_host == "http://vllm:8000"


# --- REFLEX_MODEL ---------------------------------------------------------------------


def test_an_unset_reflex_model_follows_the_openai_compat_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REFLEX_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "m")
    assert AlfredConfig.from_env().reflex_model == "vllm:m"


def test_an_explicit_reflex_model_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFLEX_MODEL", " openrouter:vendor/small ")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "m")
    assert AlfredConfig.from_env().reflex_model == "openrouter:vendor/small"


def test_a_blank_reflex_model_follows_the_openai_compat_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.env.example` ships the key blank, and "" is not absent."""
    monkeypatch.setenv("REFLEX_MODEL", " ")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "m")
    assert AlfredConfig.from_env().reflex_model == "vllm:m"


def test_with_both_blank_there_is_no_reflex_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFLEX_MODEL", "")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "")
    assert AlfredConfig.from_env().reflex_model == ""
