"""Preflight config validation for Alfred deployments.

`alfredctl doctor` reads your ``.env`` and reports, as a clear pass/warn/fail
checklist, whether each subsystem is configured before you ever start the stack:

- System 2 (Conscious) — the cloud LLM API key
- System 1 (Reflex)    — the local inference backend (Ollama or OpenAI-compatible)
- Home Assistant        — the long-lived token
- Memory embeddings     — the backend, model and index width; gated models need
                          HF_TOKEN in-process, and with a remote server the width
                          is checked against what it actually serves
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

    import httpx

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


# Short on purpose: doctor is a preflight, not a health check, and a server still
# loading a model should read as a warning rather than hang the CLI.
_PROBE_TIMEOUT_SECONDS = 4.0


def _host_side(url: str) -> str:
    """Rewrite a container-gateway hostname to localhost so host-side probes work.

    host.docker.internal / host.containers.internal only resolve inside a container;
    from the host the same service is on localhost.
    """
    return url.replace("host.docker.internal", "localhost").replace(
        "host.containers.internal", "localhost"
    )


def _probe(url: str, headers: dict[str, str] | None = None) -> tuple[bool, str]:
    """Best-effort GET; returns (ok, detail). Network errors are reported, not raised."""
    import httpx

    try:
        resp = httpx.get(_host_side(url), headers=headers, timeout=_PROBE_TIMEOUT_SECONDS)
    except Exception as exc:
        return False, f"unreachable ({type(exc).__name__})"
    return resp.status_code < 400, f"HTTP {resp.status_code}"


# Enough of the server's own words to explain a refusal, short enough not to paste an
# HTML error page into a table cell.
_MAX_PROBE_BODY_CHARS = 120


def _probe_body_excerpt(resp: httpx.Response) -> str:
    """One truncated line of the response body, or "" when it says nothing."""
    text = " ".join(resp.text.split())
    if not text:
        return ""
    if len(text) > _MAX_PROBE_BODY_CHARS:
        text = text[:_MAX_PROBE_BODY_CHARS] + "…"
    return f": {text}"


def _probe_embedding_dim(
    host: str, model: str, api_key: str, client: httpx.Client | None = None
) -> tuple[int | None, Status, str]:
    """Embed one string; return (served width, verdict, detail). No width means no answer.

    A POST to /v1/embeddings rather than a GET of /v1/models because the width the
    server actually emits is ground truth about the pair (server, model) — it settles
    what EMBEDDING_DIM only asserts, and a chat-only server (vLLM without
    ``--runner pooling``) refuses it while happily listing the model.

    The verdict answers one question, and a status added here later belongs in whichever
    bucket that question puts it in: did the probe **prove** the configuration cannot
    work, or did it only **fail to confirm** it?

    ``fail`` — proof:

    * **404 / 405.** This host has no /v1/embeddings route at all, so EMBEDDING_HOST
      names the wrong server. Not hypothetical: a box running the chat model on :8000
      and the embedding model on :8001 answers exactly this way when the two are
      transposed, while GET /v1/models on :8000 still returns 200.

    ``warn`` — inconclusive, so the operator is told rather than blamed:

    * **401 / 403.** A missing or wrong EMBEDDING_API_KEY and a wrong host are
      indistinguishable from out here.
    * **5xx.** The server exists and may be mid-load — a vLLM still reading weights
      answers this way, and it will pass a minute later.
    * **400.** Servers do not agree on what it means (unknown model, malformed body,
      input too long), and treating it as proof would fail configurations that work,
      so the body is quoted instead of judged.
    * **Connect errors and timeouts**, and a 200 that is not an embeddings response
      (a proxy, a login page): nothing was measured either way.
    """
    import httpx

    url = _host_side(f"{host.rstrip('/')}/v1/embeddings")
    key = api_key.strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": model, "input": "alfredctl doctor probe"}
    try:
        if client is not None:
            resp = client.post(url, json=body, headers=headers)
        else:
            with httpx.Client(timeout=_PROBE_TIMEOUT_SECONDS) as owned:
                resp = owned.post(url, json=body, headers=headers)
    except Exception as exc:
        return None, "warn", f"{url} unreachable ({type(exc).__name__})"
    if resp.status_code in (404, 405):
        return (
            None,
            "fail",
            (
                f"{url} answered HTTP {resp.status_code} — this host serves no embeddings "
                f"route, so EMBEDDING_HOST names the wrong server"
            ),
        )
    if resp.status_code >= 400:
        return None, "warn", f"{url} answered HTTP {resp.status_code}{_probe_body_excerpt(resp)}"
    try:
        width = len(resp.json()["data"][0]["embedding"])
    except Exception:
        return (
            None,
            "warn",
            f"{url} answered HTTP {resp.status_code}, but not an embeddings response",
        )
    return width, "pass", f"HTTP {resp.status_code}"


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


def _embedding_dim_note(model: str, dim: int, raw_dim: str) -> tuple[Status, str]:
    """How much doctor actually knows about the configured width, and how sure it is.

    Three different states used to print identically as ``dim=384``: the width this
    build knows the model emits, a width it cannot check, and a fallback that is a
    guess about a model it has never heard of. Only the first is a fact.
    """
    from shared.config import known_embedding_dim

    known = known_embedding_dim(model)
    if known is not None and known != dim:
        return "fail", (
            f"EMBEDDING_DIM={dim} but {model} emits {known} — the vector store refuses to "
            f"start on a width mismatch; unset EMBEDDING_DIM to track the model"
        )
    if known is not None:
        return "pass", f"dim={dim}"
    # The model is named by the caller, so these two do not repeat it.
    if raw_dim.strip():
        return "pass", f"dim={dim} unverified (model not in this build's dim table)"
    return "warn", (
        f"dim={dim} assumed — model unknown here, so set EMBEDDING_DIM to the width it "
        f"emits or memory search breaks on the first embed"
    )


def _check_embeddings(env: dict[str, str], online: bool) -> DoctorCheck:
    """Report the embedding backend the services will actually build.

    Every value is resolved through the same ``shared.config`` helpers ``AlfredConfig``
    uses, so doctor cannot describe a configuration the runtime would read differently —
    and the three that ``from_env`` rejects outright (an unknown backend, an unparseable
    timeout, an unparseable dim) are reported here as failures rather than echoed back
    as valid.
    """
    from shared.config import (
        DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        normalize_embedding_backend,
        normalize_embedding_dim,
        normalize_embedding_host,
        normalize_embedding_model,
        positive_seconds,
    )

    try:
        # All of it inside the try: these helpers validate, and the day one of them
        # starts raising, doctor must print a fail row rather than traceback — nothing
        # up the stack catches.
        model = normalize_embedding_model(env.get("EMBEDDING_MODEL", ""))
        backend = normalize_embedding_backend(env.get("EMBEDDING_BACKEND", ""))
        dim = normalize_embedding_dim(env.get("EMBEDDING_DIM", ""), model)
        # A blank EMBEDDING_HOST falls back to the default at config load, so calling
        # it a failure here would contradict the runtime.
        host = normalize_embedding_host(env.get("EMBEDDING_HOST", ""))
        # The timeout applies to the openai backend only, but from_env parses it
        # whatever the backend is — a bad value takes every service down, not one path.
        timeout = positive_seconds(
            "EMBEDDING_TIMEOUT_SECONDS",
            env.get("EMBEDDING_TIMEOUT_SECONDS", ""),
            DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        )
    except RuntimeError as exc:
        return DoctorCheck("memory embeddings", "fail", str(exc))

    status, note = _embedding_dim_note(model, dim, env.get("EMBEDDING_DIM", ""))
    if status == "fail":
        # A width that cannot work is the whole story — nothing after it matters.
        return DoctorCheck("memory embeddings", "fail", note)

    if backend == "openai":
        # Gating is irrelevant on this path — the server holds the weights, not us.
        where = f"via {host} (timeout {timeout:g}s)"
        if not online:
            return DoctorCheck("memory embeddings", status, f"model={model} {where}, {note}")
        served, verdict, detail = _probe_embedding_dim(
            host, model, env.get("EMBEDDING_API_KEY", "")
        )
        if served is None:
            # The verdict carries the split: fail only where the probe proved the
            # configuration cannot work, warn where it merely failed to confirm it —
            # doctor has to stay runnable from anywhere.
            return DoctorCheck("memory embeddings", verdict, f"model={model} {where} — {detail}")
        if served != dim:
            return DoctorCheck(
                "memory embeddings",
                "fail",
                f"{host} emits {served} dims for {model} but the index is built at {dim} — "
                f"the vector store refuses to start on the mismatch",
            )
        return DoctorCheck(
            "memory embeddings", "pass", f"model={model} {where}, dim={dim} confirmed by the server"
        )

    gated = model.startswith("google/embeddinggemma")
    if status == "pass" and gated and not env.get("HF_TOKEN", "").strip():
        return DoctorCheck(
            "memory embeddings",
            "warn",
            f"{model} is gated — set HF_TOKEN + accept its license, or use the ungated default",
        )
    return DoctorCheck("memory embeddings", status, f"model={model} in-process, {note}")


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
    checks.append(_check_embeddings(env, online))
    checks.append(_check_home_service())
    return checks
