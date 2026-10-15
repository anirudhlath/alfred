"""Prompt settings: env parsing and how the store is built from them."""

from __future__ import annotations

import logging

import pytest

from shared.config import DEFAULT_PROMPT_CACHE_TTL_S, AlfredConfig
from shared.prompts.store import PromptStore


def test_defaults_without_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "ALFRED_PROMPT_LABEL",
        "PROMPT_CACHE_TTL_S",
    ):
        monkeypatch.delenv(name, raising=False)
    config = AlfredConfig.from_env()
    assert (config.langfuse_host, config.prompt_label) == ("", "production")
    assert config.prompt_cache_ttl_s == DEFAULT_PROMPT_CACHE_TTL_S
    assert PromptStore.from_config(config, redis=None).describe() == "repo defaults"


def test_langfuse_settings_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse-web:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("ALFRED_PROMPT_LABEL", "candidate")
    monkeypatch.setenv("PROMPT_CACHE_TTL_S", "5")
    config = AlfredConfig.from_env()
    assert config.prompt_cache_ttl_s == 5.0
    assert PromptStore.from_config(config, redis=None).describe() == "Langfuse, label candidate"


def test_a_host_without_keys_serves_repo_defaults_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse-web:3000")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    with caplog.at_level(logging.WARNING, logger="shared.prompts.store"):
        store = PromptStore.from_config(AlfredConfig.from_env(), redis=None)
    assert store.describe() == "repo defaults"
    assert any("LANGFUSE_PUBLIC_KEY" in r.getMessage() for r in caplog.records)


def test_a_bad_ttl_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPT_CACHE_TTL_S", "soon")
    with pytest.raises(RuntimeError, match="PROMPT_CACHE_TTL_S"):
        AlfredConfig.from_env()
