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
    # These tests are about which container is targeted, not which port; give the
    # attach path a port so it gets past the guard that now refuses to assume one.
    monkeypatch.setattr(main, "_published_port", lambda exe, name: 8081)
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


def test_render_doctor_survives_markup_in_a_probe_body() -> None:
    """Details quote remote text verbatim, and rich reads `[...]` as markup.

    Observed shape: a model server naming a path in its error body. `[/models/bge-m3]`
    parses as a closing tag and raised MarkupError out of `table.add_row` — a traceback
    from the command whose whole contract is "safe to run anywhere".
    """
    checks = [
        doctor_mod.DoctorCheck(
            "memory embeddings",
            "warn",
            'HTTP 400: {"error":"model not found at [/models/bge-m3]"}',
        )
    ]
    assert main._render_doctor(checks) is False


def test_render_doctor_still_reports_failure() -> None:
    # The escape must not cost the return value the caller exits on.
    assert main._render_doctor([doctor_mod.DoctorCheck("x", "fail", "[/oops]")]) is True


class _StopBeforeDockerError(Exception):
    """Raised in place of the build, to prove the preflight print was survived."""


def test_up_preflight_survives_markup_in_a_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """`up` renders the same details `doctor` does, through its own f-string.

    A `[/...]` in a detail parses as a closing tag and raised MarkupError — the crash
    _render_doctor was fixed for, reached through the other renderer. `up` runs its
    preflight offline, so today the shape arrives from an operator-supplied .env value
    rather than a server, and the day that preflight goes online it arrives from both.
    """

    def _stop(**kwargs: object) -> None:
        raise _StopBeforeDockerError

    monkeypatch.setattr(main.rt, "detect", lambda name: APPLE)
    monkeypatch.setattr(
        main.doctor_mod,
        "run_checks",
        lambda *a, **k: [
            doctor_mod.DoctorCheck(
                "memory embeddings", "warn", "model=[/models/bge-m3] in-process, dim=384 assumed"
            )
        ],
    )
    monkeypatch.setattr(main, "build", _stop)
    with pytest.raises(_StopBeforeDockerError):
        main.up()


# --- smoke: which host port to probe -------------------------------------------------
#
# `smoke --attach` used to hardcode 8081. On a host already running Alfred there — the
# normal case for a deployment box — that probed the *other* container and reported it
# green, a pass for something nobody asked about. The port is now read from the runtime.


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("0.0.0.0:8082\n[::]:8082\n", 8082),  # docker publishes both families
        ("0.0.0.0:8081\n", 8081),
        ("[::]:9000\n", 9000),
        ("", None),  # published nothing for 8081
        ("garbage\n", None),
    ],
)
def test_published_port_reads_the_runtime(
    monkeypatch: pytest.MonkeyPatch, stdout: str, expected: int | None
) -> None:
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout, ""),
    )
    assert main._published_port("docker", "alfred-x") == expected


def test_published_port_is_none_when_the_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "No such container"),
    )
    assert main._published_port("docker", "alfred-gone") is None


def test_smoke_attach_probes_the_containers_own_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression: attaching to a container on 8082 must not probe 8081."""
    _stub_smoke_deps(monkeypatch, [])
    monkeypatch.setattr(main, "_published_port", lambda exe, name: 8082)
    seen: list[str] = []

    def _capture(
        exe: str, name: str, base_url: str, **kwargs: object
    ) -> list[smoke_mod.SmokeCheck]:
        seen.append(base_url)
        return [smoke_mod.SmokeCheck("health", True, "ok")]

    monkeypatch.setattr(smoke_mod, "run_checks", _capture)
    main.smoke(runtime=None, attach=True, name="alfred-other", timeout=1.0)
    assert seen == ["http://localhost:8082"]


def test_smoke_attach_fails_loudly_when_the_port_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back to 8081 here is what produced a green result for the wrong container."""
    _stub_smoke_deps(monkeypatch, [])
    monkeypatch.setattr(main, "_published_port", lambda exe, name: None)
    monkeypatch.setattr(
        smoke_mod, "run_checks", lambda *a, **k: pytest.fail("must not probe an assumed port")
    )
    with pytest.raises(typer.BadParameter, match="could not determine which host port"):
        main.smoke(runtime=None, attach=True, name="alfred-stopped", timeout=1.0)


def test_smoke_forwards_its_port_to_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """--port exists so a smoke run can coexist with an Alfred already on 8081."""
    fake_runtime = Runtime("docker", "docker")
    monkeypatch.setattr(rt, "detect", lambda preferred: fake_runtime)
    monkeypatch.setattr(main, "down", lambda runtime=None: None)
    up_kwargs: dict[str, object] = {}
    monkeypatch.setattr(main, "up", lambda **kwargs: up_kwargs.update(kwargs))
    seen: list[str] = []

    def _capture(
        exe: str, name: str, base_url: str, **kwargs: object
    ) -> list[smoke_mod.SmokeCheck]:
        seen.append(base_url)
        return [smoke_mod.SmokeCheck("health", True, "ok")]

    monkeypatch.setattr(smoke_mod, "run_checks", _capture)
    main.smoke(runtime=None, port=8082, timeout=1.0)
    assert up_kwargs["port"] == 8082
    assert seen == ["http://localhost:8082"]


def test_smoke_rejects_port_with_attach() -> None:
    """Contradictory: --attach reads the port off a container that already chose one."""
    with pytest.raises(typer.BadParameter, match="--port does not apply with --attach"):
        main.smoke(runtime=None, attach=True, port=8082)
