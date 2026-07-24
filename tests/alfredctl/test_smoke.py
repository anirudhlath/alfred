from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from alfredctl import smoke

if TYPE_CHECKING:
    import pytest


def test_all_checks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_http_get", lambda url, timeout=3.0: (200, "text/html", "<html>"))
    monkeypatch.setattr(smoke, "_exec_in", lambda exe, name, *cmd: (0, "PONG\nsearch"))
    checks = smoke.run_checks("docker", "alfred-x", "http://localhost:8081", timeout=1.0)
    assert all(c.passed for c in checks)
    assert [c.name for c in checks] == [
        "health",
        "redis",
        "redisearch",
        "mqtt",
        "spa",
        "data-dir",
    ]


def test_health_timeout_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_http_get", lambda url, timeout=3.0: (503, "", ""))
    monkeypatch.setattr(smoke, "_exec_in", lambda exe, name, *cmd: (0, "PONG\nsearch"))
    monkeypatch.setattr(smoke, "_POLL_INTERVAL", 0.01)
    checks = smoke.run_checks("docker", "alfred-x", "http://x", timeout=0.05)
    assert checks[0].name == "health" and not checks[0].passed
    assert len(checks) == 1


def test_redis_failure_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_http_get", lambda url, timeout=3.0: (200, "text/html", "ok"))

    def _exec(exe: str, name: str, *cmd: str) -> tuple[int, str]:
        return (1, "") if cmd[0] == "redis-cli" else (0, "ok")

    monkeypatch.setattr(smoke, "_exec_in", _exec)
    checks = smoke.run_checks("docker", "alfred-x", "http://x", timeout=1.0)
    by_name = {c.name: c for c in checks}
    assert not by_name["redis"].passed
    assert by_name["mqtt"].passed


def test_spa_check_rejects_non_html_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_http(url: str, timeout: float = 3.0) -> tuple[int, str, str]:
        return 200, "application/json", "{}"

    monkeypatch.setattr(smoke, "_http_get", _fake_http)
    monkeypatch.setattr(smoke, "_exec_in", lambda exe, name, *cmd: (0, "PONG\nsearch"))
    checks = smoke.run_checks("docker", "alfred-x", "http://x", timeout=1.0)
    by_name = {c.name: c for c in checks}
    assert not by_name["spa"].passed


def test_data_dir_check_reports_missing_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_http_get", lambda url, timeout=3.0: (200, "text/html", "ok"))

    def _exec(exe: str, name: str, *cmd: str) -> tuple[int, str]:
        if cmd[0] == "sh":
            return 1, "No such file or directory"
        return 0, "PONG\nsearch"

    monkeypatch.setattr(smoke, "_exec_in", _exec)
    checks = smoke.run_checks("docker", "alfred-x", "http://x", timeout=1.0)
    by_name = {c.name: c for c in checks}
    assert not by_name["data-dir"].passed


def test_health_poll_skips_sleep_past_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A too-short poll interval relative to the remaining timeout must not sleep past
    the deadline — the guard should skip the final sleep once it can't help anyway."""
    monkeypatch.setattr(smoke, "_http_get", lambda url, timeout=3.0: (503, "", ""))
    monkeypatch.setattr(smoke, "_exec_in", lambda exe, name, *cmd: (0, "PONG\nsearch"))
    monkeypatch.setattr(smoke, "_POLL_INTERVAL", 10.0)
    sleep_calls: list[float] = []
    monkeypatch.setattr(smoke.time, "sleep", lambda s: sleep_calls.append(s))
    checks = smoke.run_checks("docker", "alfred-x", "http://x", timeout=0.01)
    assert checks[0].name == "health" and not checks[0].passed
    assert sleep_calls == []


def test_exec_in_timeout_returns_124(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["exec"], timeout=smoke._EXEC_TIMEOUT)

    monkeypatch.setattr(smoke.subprocess, "run", _raise_timeout)
    rc, out = smoke._exec_in("docker", "alfred-x", "redis-cli", "ping")
    assert rc == 124
    assert out == "timed out"


def test_exec_in_passes_timeout_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(smoke.subprocess, "run", _fake_run)
    rc, out = smoke._exec_in("docker", "alfred-x", "redis-cli", "ping")
    assert rc == 0
    assert out == "ok"
    assert captured["timeout"] == smoke._EXEC_TIMEOUT
