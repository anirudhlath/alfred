"""Preflight config validation for Alfred deployments.

`alfredctl doctor` reads your ``.env`` and reports, as a clear pass/warn/fail
checklist, whether each subsystem is configured before you ever start the stack:

- System 2 (Conscious) — the cloud LLM API key
- System 1 (Reflex)    — the local inference backend (Ollama or OpenAI-compatible)
- Home Assistant        — the long-lived token
- Memory embeddings     — ungated by default; gated models need HF_TOKEN
- Build prerequisites    — the home-service sibling repo

Live probes (``--online``) are best-effort: a network failure downgrades to a
warning, never a hard failure, so ``doctor`` is safe to run anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from dotenv import dotenv_values

if TYPE_CHECKING:
    from pathlib import Path

Status = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class DoctorCheck:
    """One preflight check outcome."""

    name: str
    status: Status
    detail: str


def load_env(env_file: Path) -> dict[str, str]:
    """Merge process env with ``.env`` (``.env`` wins, matching container env_file)."""
    import os

    env: dict[str, str] = {k: v for k, v in os.environ.items()}
    for key, value in dotenv_values(env_file).items():
        if value is not None:
            env[key] = value
    return env


def _probe(url: str, headers: dict[str, str] | None = None) -> tuple[bool, str]:
    """Best-effort GET; returns (ok, detail). Network errors are reported, not raised."""
    import httpx

    # host.docker.internal / host.containers.internal only resolve inside a container;
    # from the host the service is on localhost. Rewrite so host-side probes work.
    probe_url = url.replace("host.docker.internal", "localhost").replace(
        "host.containers.internal", "localhost"
    )
    try:
        resp = httpx.get(probe_url, headers=headers, timeout=4.0)
    except Exception as exc:
        return False, f"unreachable ({type(exc).__name__})"
    return resp.status_code < 400, f"HTTP {resp.status_code}"


def _check_conscious(env: dict[str, str], online: bool) -> DoctorCheck:
    model = env.get("CLAUDE_MODEL", "openrouter/anthropic/claude-sonnet-4")
    openrouter = env.get("OPENROUTER_API_KEY", "").strip()
    anthropic = env.get("CLAUDE_API_KEY", "").strip()
    key = openrouter or anthropic
    if not key or key.startswith("__"):
        return DoctorCheck(
            "conscious (System 2)",
            "fail",
            "no OPENROUTER_API_KEY / CLAUDE_API_KEY — cloud reasoning disabled",
        )
    if model.startswith("openrouter/") and not openrouter:
        return DoctorCheck(
            "conscious (System 2)",
            "warn",
            f"CLAUDE_MODEL={model} needs OPENROUTER_API_KEY, only CLAUDE_API_KEY set",
        )
    if online and openrouter:
        ok, detail = _probe(
            "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {openrouter}"}
        )
        if not ok:
            return DoctorCheck(
                "conscious (System 2)", "warn", f"key set but probe failed: {detail}"
            )
        return DoctorCheck("conscious (System 2)", "pass", f"OpenRouter key valid ({detail})")
    return DoctorCheck("conscious (System 2)", "pass", f"key set, model={model}")


def _check_reflex(env: dict[str, str], online: bool) -> DoctorCheck:
    backend = env.get("REFLEX_BACKEND", "ollama").strip().lower()
    if backend == "openai":
        host = env.get("OPENAI_COMPAT_HOST", "").strip()
        model = env.get("OPENAI_COMPAT_MODEL", "").strip()
        if not host or not model:
            return DoctorCheck(
                "reflex (System 1)",
                "fail",
                "REFLEX_BACKEND=openai needs OPENAI_COMPAT_HOST and OPENAI_COMPAT_MODEL",
            )
        if online:
            ok, detail = _probe(f"{host.rstrip('/')}/v1/models")
            status: Status = "pass" if ok else "warn"
            return DoctorCheck("reflex (System 1)", status, f"openai backend {host} ({detail})")
        return DoctorCheck("reflex (System 1)", "pass", f"openai backend {host}, model={model}")
    host = env.get("OLLAMA_HOST", "http://localhost:11434").strip()
    if online:
        ok, detail = _probe(f"{host.rstrip('/')}/api/tags")
        if not ok:
            return DoctorCheck(
                "reflex (System 1)", "warn", f"Ollama at {host} unreachable: {detail}"
            )
        return DoctorCheck("reflex (System 1)", "pass", f"Ollama reachable ({detail})")
    return DoctorCheck("reflex (System 1)", "pass", f"ollama backend {host}")


def _check_home_assistant(env: dict[str, str], online: bool) -> DoctorCheck:
    token = env.get("HA_TOKEN", "").strip()
    host = env.get("HA_HOST", "").strip()
    if not token or token.startswith("__"):
        return DoctorCheck(
            "home assistant",
            "warn",
            "no HA_TOKEN — home control disabled (mint one in HA → profile → Security)",
        )
    if online and host:
        ok, detail = _probe(
            f"{host.rstrip('/')}/api/", headers={"Authorization": f"Bearer {token}"}
        )
        if not ok:
            return DoctorCheck("home assistant", "warn", f"token set but {host} probe: {detail}")
        return DoctorCheck("home assistant", "pass", f"reachable ({detail})")
    return DoctorCheck("home assistant", "pass", "token set")


def _check_embeddings(env: dict[str, str]) -> DoctorCheck:
    from shared.config import DEFAULT_EMBEDDING_MODEL

    model = env.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
    gated = model.startswith("google/embeddinggemma")
    if gated and not env.get("HF_TOKEN", "").strip():
        return DoctorCheck(
            "memory embeddings",
            "warn",
            f"{model} is gated — set HF_TOKEN + accept its license, or use the ungated default",
        )
    return DoctorCheck("memory embeddings", "pass", f"model={model}")


def _check_home_service() -> DoctorCheck:
    from alfredctl import staging

    try:
        path = staging.home_service_dir()
    except Exception as exc:
        return DoctorCheck("home-service sibling", "warn", f"could not resolve ({exc})")
    if path.is_dir():
        return DoctorCheck("home-service sibling", "pass", str(path))
    return DoctorCheck(
        "home-service sibling", "warn", "missing — `alfredctl build` clones it automatically"
    )


def run_checks(env_file: Path, *, online: bool = True) -> list[DoctorCheck]:
    """Run all preflight checks and return their outcomes."""
    checks: list[DoctorCheck] = []
    if not env_file.is_file():
        checks.append(
            DoctorCheck(".env", "fail", f"{env_file} missing — run `cp .env.example .env`")
        )
        return checks
    checks.append(DoctorCheck(".env", "pass", str(env_file)))
    env = load_env(env_file)
    checks.append(_check_conscious(env, online))
    checks.append(_check_reflex(env, online))
    checks.append(_check_home_assistant(env, online))
    checks.append(_check_embeddings(env))
    checks.append(_check_home_service())
    return checks
