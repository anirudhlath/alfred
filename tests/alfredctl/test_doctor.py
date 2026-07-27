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
