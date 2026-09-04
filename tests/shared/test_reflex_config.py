"""REFLEX_BACKEND=openai resolves its host through a fallback chain, not raw getenv."""

from __future__ import annotations

import pytest  # noqa: TC002

from shared.config import AlfredConfig


def test_blank_openai_compat_host_falls_back_to_lmstudio(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` ships the key blank, and "" is not absent.

    Without the guard the reflex client builds ``"" + "/v1/chat/completions"`` and httpx
    raises UnsupportedProtocol on a schemeless URL — the same failure
    ``normalize_embedding_host`` exists to prevent on the memory side.
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
