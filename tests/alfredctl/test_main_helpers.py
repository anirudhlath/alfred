from __future__ import annotations

import io
import json
import stat
import subprocess
from typing import TYPE_CHECKING

import pytest
import typer
from rich.console import Console

from alfredctl import doctor as doctor_mod
from alfredctl import launch, main
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


def _stub_up_deps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, notes: tuple[str, ...] = ()
) -> None:
    """Stand up everything `up()` touches outside the container runtime: runtime
    detection, the offline preflight, the repo root, and the plan itself."""
    monkeypatch.setattr(rt, "detect", lambda preferred: Runtime("docker", "docker"))
    monkeypatch.setattr(main.doctor_mod, "run_checks", lambda *a, **k: [])
    monkeypatch.setattr(main.staging, "repo_root", lambda: tmp_path)
    plan = LaunchPlan(
        run_args=[],
        url_hint="http://localhost:8081",
        name="alfred-x",
        image="alfred:x",
        notes=notes,
    )
    monkeypatch.setattr(main.launch, "build_plan", lambda *a, **k: plan)
    monkeypatch.setattr(main, "_resolve_url", lambda r, plan: "http://localhost:8081")


def _run_up(tmp_path: Path) -> None:
    main.up(mode="ephemeral", models=tmp_path / "models", do_build=False)


def test_up_prints_plan_notes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`plan.notes` is the only channel for decisions invisible in `run_args` — today
    that is the withheld container subnet, which costs the operator host access to
    passkey registration. Computing it and never showing it is the same as not having
    it."""
    _stub_up_deps(monkeypatch, tmp_path, notes=("strict-mode-note",))
    monkeypatch.setattr(main, "_run", lambda cmd, check=True: None)

    _run_up(tmp_path)

    assert "strict-mode-note" in capsys.readouterr().out


def test_up_prints_plan_notes_even_when_the_launch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Printed after `_run`, a container that fails to start swallows the note — and a
    strict-mode launch that dies on some unrelated misconfiguration is exactly when the
    operator is about to go hunting for why registration 403s."""
    _stub_up_deps(monkeypatch, tmp_path, notes=("strict-mode-note",))

    def _fail(cmd: list[str], *, check: bool = True) -> None:
        if check:  # the `rm -f` precursor passes check=False and is allowed through
            raise subprocess.CalledProcessError(125, cmd)

    monkeypatch.setattr(main, "_run", _fail)

    with pytest.raises(subprocess.CalledProcessError):
        _run_up(tmp_path)

    assert "strict-mode-note" in capsys.readouterr().out


# --- up --eval: a throwaway stack that never sees the operator's secrets --------------

DOCKER = Runtime("docker", "docker")


def _eval_plan() -> LaunchPlan:
    return LaunchPlan(run_args=[], url_hint="resolve-port", name="alfred-eval-x", image="alfred:x")


def _fake_port_run(stdout: str, code: int = 0) -> object:
    """`subprocess.run` that answers `<exe> port …` and runs anything else for real."""
    real_run = subprocess.run

    def _run(cmd: list[str], *args: object, **kwargs: object) -> object:
        if cmd[1:2] == ["port"]:
            return subprocess.CompletedProcess(cmd, code, stdout, "")
        return real_run(cmd, *args, **kwargs)

    return _run


def test_resolve_url_reads_the_eval_stacks_published_port(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "127.0.0.1:49153\n", "")

    monkeypatch.setattr(main.subprocess, "run", _fake_run)
    assert main._resolve_url(DOCKER, _eval_plan()) == "http://127.0.0.1:49153"
    assert calls == [["docker", "port", "alfred-eval-x", "8081"]]


@pytest.mark.parametrize(("code", "stdout"), [(1, ""), (0, ""), (0, "garbage\n")])
def test_resolve_url_eval_falls_back_to_the_command_that_answers(
    monkeypatch: pytest.MonkeyPatch, code: int, stdout: str
) -> None:
    """Never `localhost:8081`: on a host running the deployed stack that is the wrong one."""
    monkeypatch.setattr(main.subprocess, "run", _fake_port_run(stdout, code))
    result = main._resolve_url(DOCKER, _eval_plan())
    assert "docker port alfred-eval-x 8081" in result
    assert "8081" not in result.replace("docker port alfred-eval-x 8081", "")


def test_resolve_url_eval_survives_a_missing_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(main.subprocess, "run", _missing)
    assert "docker port alfred-eval-x 8081" in main._resolve_url(DOCKER, _eval_plan())


def _stub_eval_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    """Everything `up --eval` touches outside the plan, stubbed; returns the commands
    `_run` was handed (none of them run). The repo carries a `.env` holding a secret."""
    monkeypatch.setattr(rt, "detect", lambda preferred: DOCKER)
    monkeypatch.setattr(
        main.doctor_mod,
        "run_checks",
        lambda *a, **k: pytest.fail("eval mode must not run the .env preflight"),
    )
    monkeypatch.setattr(main.staging, "repo_root", lambda: tmp_path)
    (tmp_path / ".env").write_text(f"HA_TOKEN={_SECRET_MARK}-ha\n")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", f"{_SECRET_MARK}-passphrase")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    ran: list[list[str]] = []
    monkeypatch.setattr(main, "_run", lambda cmd, check=True: ran.append(cmd))
    monkeypatch.setattr(main.subprocess, "run", _fake_port_run("127.0.0.1:49153\n"))
    return ran


def test_up_eval_launches_without_the_operators_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = _stub_eval_up(monkeypatch, tmp_path)
    persist = tmp_path / "eval-data"

    main.up(eval_mode=True, persist=persist, models=tmp_path / "models", do_build=False)

    rm, run = ran
    assert rm[1:3] == ["rm", "-f"] and rm[3].startswith("alfred-eval-")
    assert run[1] == "run" and run[run.index("--name") + 1] == rm[3]
    assert "127.0.0.1::8081" in run and "127.0.0.1::6379" in run
    assert f"{persist.resolve()}:/data" in run
    assert f"ALFRED_SECRETS_PASSPHRASE={launch.EVAL_SECRETS_PASSPHRASE}" in run
    assert not any(_SECRET_MARK in arg for arg in run)
    # The fixed passphrase is not persisted, and a later real run there gets its own.
    assert not (persist / ".secrets-passphrase").exists()
    assert "http://127.0.0.1:49153" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["expose_ha", "expose_home"])
def test_up_eval_rejects_the_expose_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str
) -> None:
    ran = _stub_eval_up(monkeypatch, tmp_path)
    with pytest.raises(typer.BadParameter, match="expose"):
        main.up(
            eval_mode=True,
            persist=tmp_path / "eval-data",
            do_build=False,
            expose_ha=flag == "expose_ha",
            expose_home=flag == "expose_home",
        )
    assert ran == []


def test_up_eval_refuses_a_custom_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ignoring it would leave the operator expecting a stack on a port it never bound."""
    ran = _stub_eval_up(monkeypatch, tmp_path)
    with pytest.raises(typer.BadParameter, match="--eval picks a random loopback port"):
        main.up(eval_mode=True, persist=tmp_path / "eval-data", port=8082, do_build=False)
    assert ran == []


def test_up_eval_needs_a_persist_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ran = _stub_eval_up(monkeypatch, tmp_path)
    with pytest.raises(typer.BadParameter, match="--persist"):
        main.up(eval_mode=True, do_build=False)
    assert ran == []


def test_up_eval_refuses_another_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ran = _stub_eval_up(monkeypatch, tmp_path)
    with pytest.raises(typer.BadParameter, match="--mode"):
        main.up(eval_mode=True, persist=tmp_path / "eval-data", mode="seed", do_build=False)
    assert ran == []


# --- _run: what the echoed command may show ------------------------------------------
#
# `_run` prints the command before running it, and for `up` that command carries the
# whole merged env as `-e KEY=value`. The echo is for reproducing a run by hand; it
# must not hand the operator's secrets to a scrollback, a screenshot or a CI log.

# Every secret below carries this marker, so one assertion covers all of them.
_SECRET_MARK = "s3cret"
_ENV_SECRETS = {
    "OPENROUTER_API_KEY": f"sk-or-v1-{_SECRET_MARK}-openrouter",
    "HA_TOKEN": f"{_SECRET_MARK}-ha-long-lived-token",
}


def _capture_run(monkeypatch: pytest.MonkeyPatch) -> tuple[io.StringIO, list[list[str]]]:
    """Point `_run` at a console we can read and a runtime that only records argv."""
    echoed = io.StringIO()
    # soft_wrap: a wrapped line could split a secret across two lines and let a
    # `not in` assertion pass for the wrong reason.
    monkeypatch.setattr(main, "console", Console(file=echoed, soft_wrap=True))
    executed: list[list[str]] = []

    def _record(cmd: list[str], *, check: bool) -> subprocess.CompletedProcess[bytes]:
        executed.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(main.subprocess, "run", _record)
    return echoed, executed


def test_run_never_echoes_a_secret_build_plan_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every route a secret takes into `run_args`: the env file, a basic-auth host,
    HF_TOKEN from the shell, an operator `--env`, and the secrets passphrase."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in _ENV_SECRETS.items())
        # A password holding an @, on a key the gateway rewrite also touches.
        + f"EMBEDDING_HOST=http://alfred:hunter2-{_SECRET_MARK}@x@localhost:8001\n"
    )
    monkeypatch.setenv("HF_TOKEN", f"hf_{_SECRET_MARK}_token")
    docker = Runtime("docker", "docker")
    plan = launch.build_plan(
        docker,
        mode="ephemeral",
        persist=None,
        models=tmp_path / "models",
        hf_cache=None,
        expose_ha=False,
        expose_home=False,
        port=8081,
        extra_env=[f"EXTRA_TOKEN={_SECRET_MARK}-from-the-cli"],
        env_file=env_file,
        passphrase=f"{_SECRET_MARK}-passphrase",
    )
    echoed, executed = _capture_run(monkeypatch)
    cmd = [docker.exe, *plan.run_args]
    expected = list(cmd)

    main._run(cmd)

    line = echoed.getvalue()
    assert _SECRET_MARK not in line
    # Display only: the runtime still receives every value, unchanged.
    assert executed == [expected]
    # Two .env keys, the basic-auth host, HF_TOKEN, the --env pair, the passphrase.
    assert sum(_SECRET_MARK in arg for arg in executed[0]) == 6
    assert all(f"{key}={value}" in executed[0] for key, value in _ENV_SECRETS.items())
    # Still a reproducible command: flags, name, image and keys survive, and the
    # redaction reads as redaction rather than as a value.
    assert line.startswith(f"$ docker run --detach --name {plan.name} ")
    assert line.rstrip().endswith(f" {plan.image}")
    assert "-e OPENROUTER_API_KEY=*** " in line
    assert "-e ALFRED_SECRETS_PASSPHRASE=*** " in line
    assert line.count("=***") == plan.run_args.count("-e")


def test_run_masks_url_userinfo_outside_env_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    echoed, executed = _capture_run(monkeypatch)
    cmd = ["docker", "build", "-t", "alfred:x", "https://alfred:hunter2@git.example/a.git"]

    main._run(cmd)

    assert "hunter2" not in echoed.getvalue()
    assert "https://***@git.example/a.git" in echoed.getvalue()
    assert executed == [["docker", "build", "-t", "alfred:x", cmd[-1]]]


def test_run_echoes_markup_lookalikes_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path is not rich markup: `[/x]` read as a closing tag raised MarkupError
    before the command ever ran."""
    echoed, executed = _capture_run(monkeypatch)

    main._run(["docker", "build", "/tmp/[/x]"])

    assert "$ docker build /tmp/[/x]" in echoed.getvalue()
    assert executed == [["docker", "build", "/tmp/[/x]"]]
