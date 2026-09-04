"""alfredctl doctor preflight — offline config validation."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

from alfredctl import doctor


def _write_env(tmp_path: Path, body: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(body)
    return env


def _status(checks: list[doctor.DoctorCheck], name: str) -> str:
    return next(c.status for c in checks if c.name == name)


def _detail(checks: list[doctor.DoctorCheck], name: str) -> str:
    return next(c.detail for c in checks if c.name == name)


def test_missing_env_fails(tmp_path: Path) -> None:
    checks = doctor.run_checks(tmp_path / ".env", online=False)
    assert checks[0].name == ".env"
    assert checks[0].status == "fail"


def test_missing_conscious_key_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    env = _write_env(tmp_path, "REFLEX_BACKEND=ollama\n")
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "conscious (System 2)") == "fail"


def test_placeholder_key_fails(tmp_path: Path) -> None:
    env = _write_env(tmp_path, "OPENROUTER_API_KEY=__FILL_ME__\n")
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "conscious (System 2)") == "fail"


def test_valid_key_passes_offline(tmp_path: Path) -> None:
    env = _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-v1-abc123\n")
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "conscious (System 2)") == "pass"


def test_openai_backend_requires_host_and_model(tmp_path: Path) -> None:
    env = _write_env(
        tmp_path,
        "OPENROUTER_API_KEY=sk-or-v1-abc\nREFLEX_BACKEND=openai\nOPENAI_COMPAT_HOST=\n",
    )
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "reflex (System 1)") == "fail"


def test_missing_ha_token_warns(tmp_path: Path) -> None:
    env = _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-v1-abc\nHA_TOKEN=\n")
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "home assistant") == "warn"


def test_gated_embedding_without_token_warns(tmp_path: Path) -> None:
    env = _write_env(
        tmp_path,
        "OPENROUTER_API_KEY=sk-or-v1-abc\nEMBEDDING_MODEL=google/embeddinggemma-300m\nHF_TOKEN=\n",
    )
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "warn"


def test_default_embedding_passes(tmp_path: Path) -> None:
    env = _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-v1-abc\n")
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "pass"


def test_openai_embedding_backend_reports_the_host(tmp_path: Path) -> None:
    """The remote server holds the weights, so doctor must name it, not just the model."""
    env = _write_env(
        tmp_path,
        "OPENROUTER_API_KEY=sk-or-v1-abc\n"
        "EMBEDDING_BACKEND=openai\n"
        # Written the way vLLM prints its base URL — the /v1 is stripped for the caller.
        "EMBEDDING_HOST=http://vllm.example:8001/v1\n",
    )
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "pass"
    assert "http://vllm.example:8001" in _detail(checks, "memory embeddings")
    assert "/v1" not in _detail(checks, "memory embeddings")


def test_blank_embedding_host_reports_the_runtime_default(tmp_path: Path) -> None:
    """`EMBEDDING_HOST=` in .env is "" — the runtime falls back, so doctor must too."""
    from shared.config import DEFAULT_EMBEDDING_HOST

    env = _write_env(
        tmp_path,
        "OPENROUTER_API_KEY=sk-or-v1-abc\nEMBEDDING_BACKEND=openai\nEMBEDDING_HOST=\n",
    )
    checks = doctor.run_checks(env, online=False)
    assert DEFAULT_EMBEDDING_HOST in _detail(checks, "memory embeddings")


def test_gated_model_needs_no_token_on_the_openai_backend(tmp_path: Path) -> None:
    """Nothing is downloaded locally, so warning about HF_TOKEN would be wrong."""
    env = _write_env(
        tmp_path,
        "OPENROUTER_API_KEY=sk-or-v1-abc\n"
        "EMBEDDING_BACKEND=openai\n"
        "EMBEDDING_MODEL=google/embeddinggemma-300m\n"
        "HF_TOKEN=\n",
    )
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "pass"


def test_unknown_embedding_backend_fails(tmp_path: Path) -> None:
    """Config load rejects it, so every service dies at startup — doctor says so first."""
    env = _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-v1-abc\nEMBEDDING_BACKEND=vllm\n")
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "fail"
    assert "EMBEDDING_BACKEND" in _detail(checks, "memory embeddings")


def test_unparseable_embedding_timeout_fails(tmp_path: Path) -> None:
    """Same reason: AlfredConfig.from_env raises on it whatever the backend is."""
    env = _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-v1-abc\nEMBEDDING_TIMEOUT_SECONDS=abc\n")
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "fail"
    assert "EMBEDDING_TIMEOUT_SECONDS" in _detail(checks, "memory embeddings")


def test_blank_embedding_model_reports_the_runtime_default(tmp_path: Path) -> None:
    """`.env.example` ships `EMBEDDING_MODEL=` blank; doctor must not print `model=`."""
    from shared.config import DEFAULT_EMBEDDING_MODEL

    env = _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-v1-abc\nEMBEDDING_MODEL=\n")
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "pass"
    assert DEFAULT_EMBEDDING_MODEL in _detail(checks, "memory embeddings")


def test_unparseable_embedding_dim_fails(tmp_path: Path) -> None:
    """The index width is the one value a mismatch corrupts silently — never guess it."""
    env = _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-v1-abc\nEMBEDDING_DIM=abc\n")
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "fail"
    assert "EMBEDDING_DIM" in _detail(checks, "memory embeddings")
