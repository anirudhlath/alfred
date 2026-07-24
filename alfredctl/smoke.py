"""Containerized smoke checks: is a running Alfred container actually alive?"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

_POLL_INTERVAL = 2.0
_EXEC_TIMEOUT = 30.0


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    passed: bool
    detail: str


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, str, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read(2048).decode(errors="replace")
            return resp.status, resp.headers.get("content-type", ""), body
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception as e:
        return 0, "", str(e)


def _exec_in(exe: str, name: str, *cmd: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            [exe, "exec", name, *cmd],
            capture_output=True,
            text=True,
            check=False,
            timeout=_EXEC_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return proc.returncode, proc.stdout + proc.stderr


def run_checks(exe: str, name: str, base_url: str, timeout: float = 300.0) -> list[SmokeCheck]:
    """Boot-gate + verify the containerized stack. `timeout` bounds only the initial
    /health poll (a startup gate, not steady-state polling) — every other check runs once.
    """
    checks: list[SmokeCheck] = []

    deadline = time.monotonic() + timeout
    status = 0
    while time.monotonic() < deadline:
        status, _, _ = _http_get(f"{base_url}/health")
        if status == 200:
            break
        if time.monotonic() + _POLL_INTERVAL < deadline:
            time.sleep(_POLL_INTERVAL)
    checks.append(SmokeCheck("health", status == 200, f"GET /health → {status}"))
    if status != 200:
        return checks  # nothing else can pass; report what we have

    rc, out = _exec_in(exe, name, "redis-cli", "ping")
    checks.append(SmokeCheck("redis", rc == 0 and "PONG" in out, out.strip()[:80]))

    rc, out = _exec_in(exe, name, "redis-cli", "MODULE", "LIST")
    checks.append(SmokeCheck("redisearch", rc == 0 and "search" in out.lower(), "MODULE LIST"))

    rc, out = _exec_in(
        exe, name, "mosquitto_pub", "-h", "localhost", "-t", "alfred/smoke", "-m", "ok"
    )
    checks.append(SmokeCheck("mqtt", rc == 0, out.strip()[:80] or "publish ok"))

    status, ctype, _ = _http_get(f"{base_url}/")
    spa_passed = status == 200 and "text/html" in ctype
    checks.append(SmokeCheck("spa", spa_passed, f"GET / → {status} {ctype}"))

    rc, out = _exec_in(exe, name, "sh", "-c", "ls /data/scratchpad.md /data/routines")
    checks.append(SmokeCheck("data-dir", rc == 0, out.strip()[:80]))

    return checks
