from __future__ import annotations

import json
import subprocess

import pytest

from alfredctl import runtime

# Real shape observed from a live `container network inspect default` on Apple's container CLI.
_LIVE_NETWORK_INSPECT_JSON = json.dumps(
    [
        {
            "id": "default",
            "status": {"ipv4Subnet": "192.168.65.0/24"},
        }
    ]
)


def test_detect_prefers_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.shutil, "which", lambda name: f"/bin/{name}")
    assert runtime.detect("podman").name == "podman"


def test_detect_order_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: f"/bin/{name}")
    assert runtime.detect(None).name == "container"


def test_detect_skips_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime.shutil, "which", lambda name: "/bin/docker" if name == "docker" else None
    )
    assert runtime.detect(None).name == "docker"


def test_detect_none_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="No container runtime"):
        runtime.detect(None)


def test_detect_rejects_unknown_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.shutil, "which", lambda name: f"/bin/{name}")
    with pytest.raises(RuntimeError, match="Unknown runtime 'nope'"):
        runtime.detect("nope")


def test_slug_sanitizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_current_branch", lambda: "worktree-feat+Container/Z")
    assert runtime.branch_slug() == "worktree-feat-container-z"


def test_gateway_per_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    assert runtime.host_gateway(runtime.Runtime("docker", "docker")) == "host.docker.internal"
    assert runtime.host_gateway(runtime.Runtime("podman", "podman")) == "host.containers.internal"


def test_apple_vmnet_gateway_reads_live_json_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=_LIVE_NETWORK_INSPECT_JSON)

    monkeypatch.setattr(runtime.subprocess, "run", _fake_run)
    apple = runtime.Runtime("container", "container")
    # Distinct from the 192.168.64.1 fallback, so this proves the live lookup path — not
    # the except/fallback branch — produced the result.
    assert runtime.host_gateway(apple) == "192.168.65.1"


def test_apple_vmnet_gateway_falls_back_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="not json")

    monkeypatch.setattr(runtime.subprocess, "run", _fake_run)
    apple = runtime.Runtime("container", "container")
    assert runtime.host_gateway(apple) == "192.168.64.1"
