"""`.env.example` is a working config, not just documentation.

`cp .env.example .env` is the documented first step, and the container passes that file
straight through as its env_file — so every key it ships blank ("leave blank to accept
the default") reaches `AlfredConfig.from_env()` as `""`, not as absent. That defeats
`os.getenv`'s own default, and the parsed keys then raise at import time in all nine
services at once. This test is the guard: the shipped file must load.
"""

from __future__ import annotations

import pytest  # noqa: TC002
from dotenv import dotenv_values

from shared import config
from shared.config import AlfredConfig


def _example_env() -> dict[str, str]:
    from alfredctl import staging

    values = dotenv_values(staging.repo_root() / ".env.example")
    return {k: v for k, v in values.items() if v is not None}


def test_env_example_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _example_env().items():
        monkeypatch.setenv(key, value)
    cfg = AlfredConfig.from_env()
    # The embedding block is the one this branch rewrote; assert it lands on the
    # documented defaults rather than on "" / 0.
    assert cfg.embedding_backend == config.DEFAULT_EMBEDDING_BACKEND
    assert cfg.embedding_model == config.DEFAULT_EMBEDDING_MODEL
    assert cfg.embedding_dim == config.embedding_dim_for(config.DEFAULT_EMBEDDING_MODEL)
    assert cfg.embedding_host == config.DEFAULT_EMBEDDING_HOST
    assert cfg.embedding_timeout_seconds == config.DEFAULT_EMBEDDING_TIMEOUT_SECONDS


def test_env_example_documents_every_embedding_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # A new EMBEDDING_* knob that never reaches .env.example is invisible to operators.
    keys = set(_example_env())
    assert {
        "EMBEDDING_BACKEND",
        "EMBEDDING_HOST",
        "EMBEDDING_API_KEY",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIM",
        "EMBEDDING_TIMEOUT_SECONDS",
    } <= keys
