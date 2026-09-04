"""The in-container runner rewrites localhost host vars to the container gateway."""

from __future__ import annotations

import pytest  # noqa: TC002

from runner.__main__ import rewrite_host_gateway


def test_noop_when_not_managed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Native dev (no ALFRED_MANAGE_INFRA) must never rewrite — localhost is correct there.
    monkeypatch.setattr("runner.__main__._reachable_gateway", lambda: "host.docker.internal")
    env = {"OLLAMA_HOST": "http://localhost:11434"}
    rewrite_host_gateway(env)
    assert env["OLLAMA_HOST"] == "http://localhost:11434"


def test_noop_when_no_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runner.__main__._reachable_gateway", lambda: None)
    env = {"ALFRED_MANAGE_INFRA": "1", "OLLAMA_HOST": "http://localhost:11434"}
    rewrite_host_gateway(env)
    assert env["OLLAMA_HOST"] == "http://localhost:11434"


def test_rewrites_localhost_and_127(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("runner.__main__._reachable_gateway", lambda: "host.docker.internal")
    env = {
        "ALFRED_MANAGE_INFRA": "1",
        "OLLAMA_HOST": "http://localhost:11434",
        "OPENAI_COMPAT_HOST": "http://127.0.0.1:8000",
        "HA_HOST": "http://192.168.1.5:8123",  # real host — left alone
    }
    rewrite_host_gateway(env)
    assert env["OLLAMA_HOST"] == "http://host.docker.internal:11434"
    assert env["OPENAI_COMPAT_HOST"] == "http://host.docker.internal:8000"
    assert env["HA_HOST"] == "http://192.168.1.5:8123"


def test_rewrites_embedding_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-container, EMBEDDING_HOST=localhost means the container, not the box."""
    monkeypatch.setattr("runner.__main__._reachable_gateway", lambda: "host.docker.internal")
    env = {
        "ALFRED_MANAGE_INFRA": "1",
        "EMBEDDING_HOST": "http://localhost:8001",
    }
    rewrite_host_gateway(env)
    assert env["EMBEDDING_HOST"] == "http://host.docker.internal:8001"
