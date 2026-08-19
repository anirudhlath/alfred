from __future__ import annotations

import json
import stat
import subprocess
from typing import TYPE_CHECKING

import pytest
import typer

from alfredctl import doctor as doctor_mod
from alfredctl import main
from alfredctl import runtime as rt
from alfredctl import smoke as smoke_mod
from alfredctl.launch import LaunchPlan
from alfredctl.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path

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


def _stub_smoke_deps(monkeypatch: pytest.MonkeyPatch, down_calls: list[str | None]) -> Runtime:
    """Stand up the collaborators `smoke()` needs (runtime detection, `up`, `down`)
    without touching a real container runtime."""
    fake_runtime = Runtime("docker", "docker")
    monkeypatch.setattr(rt, "detect", lambda preferred: fake_runtime)
    monkeypatch.setattr(main, "up", lambda **kwargs: None)
    monkeypatch.setattr(main, "down", lambda runtime=None: down_calls.append(runtime))
    return fake_runtime


def test_smoke_tears_down_on_run_checks_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A crash between `up()` and the result table (e.g. run_checks raising) must not
    leak the seed container — `down()` runs in a `finally`, and the exception still
    propagates to the caller instead of being swallowed."""
    down_calls: list[str | None] = []
    fake_runtime = _stub_smoke_deps(monkeypatch, down_calls)

    def _raise(*args: object, **kwargs: object) -> list[smoke_mod.SmokeCheck]:
        raise RuntimeError("boom")

    monkeypatch.setattr(smoke_mod, "run_checks", _raise)

    with pytest.raises(RuntimeError, match="boom"):
        main.smoke(runtime=None, keep=False, attach=False, hf_cache=None, timeout=1.0)

    assert down_calls == [fake_runtime.name]


def test_smoke_keep_skips_teardown_even_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """--keep is an explicit opt-out of teardown; it must still be honored when checks
    raise, not just on the happy path."""
    down_calls: list[str | None] = []
    _stub_smoke_deps(monkeypatch, down_calls)

    def _raise(*args: object, **kwargs: object) -> list[smoke_mod.SmokeCheck]:
        raise RuntimeError("boom")

    monkeypatch.setattr(smoke_mod, "run_checks", _raise)

    with pytest.raises(RuntimeError, match="boom"):
        main.smoke(runtime=None, keep=True, attach=False, hf_cache=None, timeout=1.0)

    assert down_calls == []


def test_smoke_happy_path_tears_down_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    down_calls: list[str | None] = []
    fake_runtime = _stub_smoke_deps(monkeypatch, down_calls)
    passing = [smoke_mod.SmokeCheck("health", True, "GET /health -> 200")]
    monkeypatch.setattr(smoke_mod, "run_checks", lambda *a, **k: passing)

    main.smoke(runtime=None, keep=False, attach=False, hf_cache=None, timeout=1.0)

    assert down_calls == [fake_runtime.name]


def test_smoke_exits_nonzero_when_checks_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    down_calls: list[str | None] = []
    _stub_smoke_deps(monkeypatch, down_calls)
    failing = [smoke_mod.SmokeCheck("health", False, "GET /health -> 503")]
    monkeypatch.setattr(smoke_mod, "run_checks", lambda *a, **k: failing)

    with pytest.raises(typer.Exit) as exc_info:
        main.smoke(runtime=None, keep=False, attach=False, hf_cache=None, timeout=1.0)

    assert exc_info.value.exit_code == 1
    assert down_calls == [Runtime("docker", "docker").name]


def _capture_smoke_name(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Run main.smoke with every side effect stubbed; capture the container it targets."""
    seen: dict[str, str] = {}

    def _fake_run_checks(
        exe: str, name: str, base_url: str, timeout: float = 300.0, *, deep: bool = False
    ) -> list[smoke_mod.SmokeCheck]:
        seen["name"] = name
        return [smoke_mod.SmokeCheck("health", True, "GET /health → 200")]

    _stub_smoke_deps(monkeypatch, [])
    monkeypatch.setattr(main, "_resolve_url", lambda r, plan: "http://localhost:8081")
    monkeypatch.setattr(main.smoke_mod, "run_checks", _fake_run_checks)
    return seen


def test_smoke_name_option_targets_that_container(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_smoke_name(monkeypatch)
    main.smoke(attach=True, name="alfred")
    assert seen["name"] == "alfred"


def test_smoke_without_name_keeps_branch_container(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_smoke_name(monkeypatch)
    monkeypatch.setattr(main.rt, "container_name", lambda: "alfred-somebranch")
    main.smoke(attach=True)
    assert seen["name"] == "alfred-somebranch"


def test_smoke_name_without_attach_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """--name only makes sense against an already-running container; without --attach,
    smoke boots its own (branch-named) container, so a --name override would silently
    check a container that was never started. Nothing in main.rt/up/down is stubbed
    here — the guard must fire before any of that runs."""
    with pytest.raises(typer.BadParameter):
        main.smoke(attach=False, name="alfred")


def _capture_doctor_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Run main.doctor with the real checks stubbed; capture the .env path it validates."""
    seen: dict[str, Path] = {}

    def _fake_run_checks(env_file: Path, *, online: bool = True) -> list[doctor_mod.DoctorCheck]:
        seen["env_file"] = env_file
        return [doctor_mod.DoctorCheck(".env", "pass", str(env_file))]

    monkeypatch.setattr(main.doctor_mod, "run_checks", _fake_run_checks)
    return seen


def test_doctor_env_file_option_overrides_repo_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _capture_doctor_env(monkeypatch)
    main.doctor(online=False, env_file=tmp_path / "deploy.env")
    assert seen["env_file"] == tmp_path / "deploy.env"


def test_doctor_without_env_file_uses_repo_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _capture_doctor_env(monkeypatch)
    monkeypatch.setattr(main.staging, "repo_root", lambda: tmp_path)
    main.doctor(online=False)
    assert seen["env_file"] == tmp_path / ".env"
