"""alfredctl doctor preflight — offline config validation."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest  # noqa: TC002

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
        "EMBEDDING_HOST=http://vllm.example:8001/v1\n"
        # Not the 30s default, so the rendering cannot pass by hardcoding it.
        "EMBEDDING_TIMEOUT_SECONDS=7.5\n",
    )
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "pass"
    detail = _detail(checks, "memory embeddings")
    assert "http://vllm.example:8001" in detail
    assert "/v1" not in detail
    assert "timeout 7.5s" in detail


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


def test_dim_disagreeing_with_the_known_model_fails(tmp_path: Path) -> None:
    """The store refuses to start on a width mismatch — doctor must not call it pass."""
    env = _write_env(
        tmp_path,
        "OPENROUTER_API_KEY=sk-or-v1-abc\nEMBEDDING_MODEL=BAAI/bge-m3\nEMBEDDING_DIM=384\n",
    )
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "fail"
    detail = _detail(checks, "memory embeddings")
    assert "384" in detail and "1024" in detail


def test_unknown_model_without_a_dim_warns_that_384_is_a_guess(tmp_path: Path) -> None:
    env = _write_env(
        tmp_path,
        "OPENROUTER_API_KEY=sk-or-v1-abc\nEMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5\n",
    )
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "warn"
    assert "EMBEDDING_DIM" in _detail(checks, "memory embeddings")


def test_unknown_model_with_an_explicit_dim_passes_but_says_unverified(tmp_path: Path) -> None:
    # The documented use of EMBEDDING_DIM. doctor cannot confirm it offline, and says so.
    env = _write_env(
        tmp_path,
        "OPENROUTER_API_KEY=sk-or-v1-abc\n"
        "EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5\n"
        "EMBEDDING_DIM=768\n",
    )
    checks = doctor.run_checks(env, online=False)
    assert _status(checks, "memory embeddings") == "pass"
    assert "unverified" in _detail(checks, "memory embeddings")


def _openai_env(**extra: str) -> dict[str, str]:
    env = {
        "EMBEDDING_BACKEND": "openai",
        "EMBEDDING_HOST": "http://vllm.example:8001",
        "EMBEDDING_MODEL": "BAAI/bge-m3",
    }
    env.update(extra)
    return env


def test_online_probe_disagreeing_with_the_configured_dim_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The served width is ground truth — it outranks both the table and EMBEDDING_DIM."""
    monkeypatch.setattr(doctor, "_probe_embedding_dim", lambda *a, **k: (768, "HTTP 200"))
    check = doctor._check_embeddings(_openai_env(), online=True)
    assert check.status == "fail"
    assert "768" in check.detail and "1024" in check.detail


def test_online_probe_confirms_the_dim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "_probe_embedding_dim", lambda *a, **k: (1024, "HTTP 200"))
    check = doctor._check_embeddings(_openai_env(), online=True)
    assert check.status == "pass"
    assert "confirmed" in check.detail


def test_unreachable_embedding_host_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    # Best-effort: doctor runs anywhere, so a network failure never hard-fails.
    monkeypatch.setattr(
        doctor, "_probe_embedding_dim", lambda *a, **k: (None, "unreachable (ConnectError)")
    )
    check = doctor._check_embeddings(_openai_env(), online=True)
    assert check.status == "warn"
    assert "unreachable" in check.detail


def test_offline_never_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> tuple[int | None, str]:
        raise AssertionError("--offline must not touch the network")

    monkeypatch.setattr(doctor, "_probe_embedding_dim", _boom)
    assert doctor._check_embeddings(_openai_env(), online=False).status == "pass"


def test_probe_reads_the_served_width() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sekret"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 1024}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        width, detail = doctor._probe_embedding_dim(
            "http://vllm.example:8001", "BAAI/bge-m3", "sekret", client=client
        )
    assert width == 1024
    assert "200" in detail


def test_probe_reports_an_http_error_rather_than_a_width() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        # What a chat-only vLLM answers on /v1/embeddings.
        return httpx.Response(400, json={"error": "model does not support embeddings"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        width, detail = doctor._probe_embedding_dim(
            "http://vllm.example:8001", "BAAI/bge-m3", "", client=client
        )
    assert width is None
    assert "400" in detail


def test_probe_survives_a_non_embeddings_response() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        # A proxy that 200s everything, say.
        return httpx.Response(200, text="<html>hello</html>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        width, detail = doctor._probe_embedding_dim(
            "http://vllm.example:8001", "BAAI/bge-m3", "", client=client
        )
    assert width is None
    assert detail
