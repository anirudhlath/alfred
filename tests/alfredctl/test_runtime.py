from __future__ import annotations

import pytest

from alfredctl import runtime


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


def test_slug_sanitizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_current_branch", lambda: "worktree-feat+Container/Z")
    assert runtime.branch_slug() == "worktree-feat-container-z"


def test_gateway_per_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    assert runtime.host_gateway(runtime.Runtime("docker", "docker")) == "host.docker.internal"
    assert runtime.host_gateway(runtime.Runtime("podman", "podman")) == "host.containers.internal"
