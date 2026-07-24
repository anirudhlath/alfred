from __future__ import annotations

import json
import stat
import subprocess
from typing import TYPE_CHECKING

from alfredctl import main
from alfredctl.launch import LaunchPlan
from alfredctl.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

APPLE = Runtime("container", "container")

# Real shape observed from a live `container inspect <name>` on Apple's container CLI.
_LIVE_INSPECT_JSON = json.dumps(
    [
        {
            "configuration": {"id": "alfred-worktree-feat-containerization"},
            "id": "alfred-worktree-feat-containerization",
            "status": {
                "networks": [
                    {
                        "ipv4Address": "192.168.64.9/24",
                        "network": "default",
                    }
                ],
                "state": "running",
            },
        }
    ]
)


def _plan() -> LaunchPlan:
    return LaunchPlan(run_args=[], url_hint="resolve-ip", name="alfred-x", image="alfred:x")


def test_resolve_url_reads_live_apple_json_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=_LIVE_INSPECT_JSON)

    monkeypatch.setattr(main.subprocess, "run", _fake_run)
    assert main._resolve_url(APPLE, _plan()) == "http://192.168.64.9:8081"


def test_resolve_url_falls_back_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="not json")

    monkeypatch.setattr(main.subprocess, "run", _fake_run)
    result = main._resolve_url(APPLE, _plan())
    assert result.startswith("http://<container-ip>:8081")


def test_passphrase_persistent_creates_atomic_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALFRED_SECRETS_PASSPHRASE", raising=False)
    value = main._passphrase("persistent", tmp_path)
    marker = tmp_path / ".secrets-passphrase"
    assert marker.is_file()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert marker.read_text().strip() == value


def test_passphrase_persistent_idempotent_no_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALFRED_SECRETS_PASSPHRASE", raising=False)
    first = main._passphrase("persistent", tmp_path)
    marker = tmp_path / ".secrets-passphrase"
    mtime_before = marker.stat().st_mtime_ns
    second = main._passphrase("persistent", tmp_path)
    assert second == first
    assert marker.stat().st_mtime_ns == mtime_before
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
