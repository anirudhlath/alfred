"""LLM role settings: defaults, the legacy CLAUDE_MODEL translation, validation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from shared import config as config_mod
from shared.config import (
    DEFAULT_SYSTEM2_MODEL,
    AlfredConfig,
    apply_model_env,
    normalize_vllm_base_url,
    resolve_llm_settings,
    translate_legacy_model,
    vllm_family_known,
)
from shared.gateway import GATEWAY_REWRITE_KEYS

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _fresh_warning() -> Iterator[None]:
    config_mod._warn_legacy_model.cache_clear()
    yield
    config_mod._warn_legacy_model.cache_clear()


def test_defaults_when_nothing_is_set() -> None:
    s = resolve_llm_settings({})
    assert s.system2_model == DEFAULT_SYSTEM2_MODEL == "openrouter:anthropic/claude-sonnet-4"
    assert s.librarian_model == s.system2_model
    assert s.vllm_base_url == "http://localhost:1234/v1"
    assert s.openrouter_api_key == ""
    assert s.system2_max_tokens == 2048
    assert s.system2_timeout_s == 60.0


def test_blank_values_mean_the_default() -> None:
    blank = dict.fromkeys(
        [
            "SYSTEM2_MODEL",
            "LIBRARIAN_MODEL",
            "VLLM_BASE_URL",
            "SYSTEM2_MAX_TOKENS",
            "SYSTEM2_TIMEOUT_S",
            "OPENROUTER_API_KEY",
            "CLAUDE_MODEL",
        ],
        " ",
    )
    assert resolve_llm_settings(blank) == resolve_llm_settings({})


@pytest.mark.parametrize(
    ("raw", "base", "want"),
    [
        ("openrouter/anthropic/claude-sonnet-4", "", "openrouter:anthropic/claude-sonnet-4"),
        ("openai/gemma-4-26b-a4b", "http://proxy:9/v1", "vllm:gemma-4-26b-a4b"),
        ("openai/gpt-x", "", "openai:gpt-x"),
        ("vllm:gemma-4-26b-a4b", "", "vllm:gemma-4-26b-a4b"),
        ("claude-sonnet-4-6", "", "claude-sonnet-4-6"),
    ],
)
def test_translate_legacy_model(raw: str, base: str, want: str) -> None:
    assert translate_legacy_model(raw, openai_base_url=base) == want


def test_system2_model_wins_over_claude_model() -> None:
    s = resolve_llm_settings({"SYSTEM2_MODEL": "vllm:qwen3", "CLAUDE_MODEL": "openrouter/x/y"})
    assert s.system2_model == "vllm:qwen3"


def test_translated_claude_model_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    env = {"CLAUDE_MODEL": "openrouter/anthropic/claude-sonnet-4"}
    with caplog.at_level(logging.WARNING, logger="shared.config"):
        first = resolve_llm_settings(env)
        resolve_llm_settings(env)
    assert first.system2_model == "openrouter:anthropic/claude-sonnet-4"
    assert caplog.text.count("is a LiteLLM model string") == 1


def test_untranslated_claude_model_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="shared.config"):
        s = resolve_llm_settings({"CLAUDE_MODEL": "vllm:gemma-x"})
    assert s.system2_model == "vllm:gemma-x"
    assert "LiteLLM" not in caplog.text


def test_legacy_openai_model_takes_its_base_url_from_openai_base_url() -> None:
    s = resolve_llm_settings(
        {
            "CLAUDE_MODEL": "openai/gemma-x",
            "OPENAI_BASE_URL": "http://proxy:9/v1/",
            "OPENAI_COMPAT_HOST": "http://other:1",
        }
    )
    assert s.system2_model == "vllm:gemma-x"
    assert s.vllm_base_url == "http://proxy:9/v1"


def test_vllm_base_url_falls_back_to_the_compat_host_chain() -> None:
    assert resolve_llm_settings({"OPENAI_COMPAT_HOST": "http://h:8000"}).vllm_base_url == (
        "http://h:8000/v1"
    )
    assert resolve_llm_settings({"LMSTUDIO_HOST": "http://lm:1234/"}).vllm_base_url == (
        "http://lm:1234/v1"
    )
    assert (
        resolve_llm_settings(
            {"VLLM_BASE_URL": "http://v:8000/v1", "OPENAI_COMPAT_HOST": "http://h:1"}
        ).vllm_base_url
        == "http://v:8000/v1"
    )


@pytest.mark.parametrize(
    ("raw", "want"),
    [("", ""), ("http://h:8000", "http://h:8000/v1"), ("http://h:8000/v1/", "http://h:8000/v1")],
)
def test_normalize_vllm_base_url(raw: str, want: str) -> None:
    assert normalize_vllm_base_url(raw) == want


def test_librarian_model_override() -> None:
    s = resolve_llm_settings({"SYSTEM2_MODEL": "vllm:a", "LIBRARIAN_MODEL": "vllm:b"})
    assert (s.system2_model, s.librarian_model) == ("vllm:a", "vllm:b")


def test_openrouter_key_falls_back_to_claude_api_key() -> None:
    assert resolve_llm_settings({"CLAUDE_API_KEY": "k2"}).openrouter_api_key == "k2"
    assert (
        resolve_llm_settings(
            {"OPENROUTER_API_KEY": "k1", "CLAUDE_API_KEY": "k2"}
        ).openrouter_api_key
        == "k1"
    )
    assert (
        resolve_llm_settings({"OPENROUTER_API_KEY": "", "CLAUDE_API_KEY": "k2"}).openrouter_api_key
        == "k2"
    )


def test_max_tokens_reads_the_new_name_then_the_legacy_one() -> None:
    assert resolve_llm_settings({"CLAUDE_MAX_TOKENS": "1000"}).system2_max_tokens == 1000
    assert (
        resolve_llm_settings(
            {"SYSTEM2_MAX_TOKENS": "500", "CLAUDE_MAX_TOKENS": "1000"}
        ).system2_max_tokens
        == 500
    )


@pytest.mark.parametrize(
    ("env", "name"),
    [
        ({"SYSTEM2_MAX_TOKENS": "lots"}, "SYSTEM2_MAX_TOKENS"),
        ({"CLAUDE_MAX_TOKENS": "0"}, "CLAUDE_MAX_TOKENS"),
        ({"SYSTEM2_TIMEOUT_S": "-1"}, "SYSTEM2_TIMEOUT_S"),
    ],
)
def test_bad_numbers_name_their_variable(env: dict[str, str], name: str) -> None:
    with pytest.raises(RuntimeError, match=name):
        resolve_llm_settings(env)


@pytest.mark.parametrize(
    ("name", "known"),
    [
        ("gemma-4-26b-a4b", True),
        ("google/gemma-3-27b", True),
        ("Qwen3-32B", True),
        ("my-served-model", False),
    ],
)
def test_vllm_family_known(name: str, known: bool) -> None:
    assert vllm_family_known(name) is known


def test_apply_model_env_exports_only_what_is_set() -> None:
    environ: dict[str, str] = {}
    apply_model_env(AlfredConfig(vllm_base_url="http://h:8000/v1", openrouter_api_key=""), environ)
    assert environ == {"VLLM_BASE_URL": "http://h:8000/v1"}


def test_from_env_reads_the_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYSTEM2_MODEL", "vllm:gemma-x")
    monkeypatch.setenv("SYSTEM2_TIMEOUT_S", "15")
    cfg = AlfredConfig.from_env()
    assert cfg.system2_model == "vllm:gemma-x"
    assert cfg.system2_timeout_s == 15.0


def test_gateway_rewrites_the_new_host_keys() -> None:
    assert {"VLLM_BASE_URL", "LANGFUSE_HOST"} <= set(GATEWAY_REWRITE_KEYS)
