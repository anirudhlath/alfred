from __future__ import annotations

import asyncio
import errno
import functools
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from inspect_ai import Task, eval_async
from inspect_ai.dataset import Sample
from inspect_ai.model import get_model
from inspect_ai.solver import solver
from inspect_ai.util import _display as inspect_display
from inspect_ai.util import display_type

from evals.harness import orchestrate
from evals.harness.fake_ha import FakeHA
from evals.harness.judge import (
    CalibrationReport,
    CategoryResult,
    calibration_digests,
    load_calibration_sets,
)
from evals.harness.orchestrate import (
    RunCancelled,
    RunOptions,
    _build_image,
    build_plan,
    calibration_for,
    execute,
    read_calibration,
    run_suites,
)
from evals.harness.preflight import PreflightError
from evals.harness.proxy import LlmProxy
from evals.harness.scenario import Scenario, ScenarioError, expand_variants, load_suites
from evals.harness.stack import StackError

if TYPE_CHECKING:
    from collections.abc import Callable

    from inspect_ai.log import EvalLog
    from inspect_ai.model import Model
    from inspect_ai.solver import Generate, Solver, TaskState


class RecordingStack:
    def __init__(self, start_error: Exception | None = None) -> None:
        self.started = self.stopped = 0
        self.boot_seconds = 1.0
        self.first_reply_ms = 100.0
        self._start_error = start_error

    async def start(self) -> None:
        self.started += 1
        if self._start_error is not None:
            raise self._start_error

    async def stop(self) -> None:
        self.stopped += 1


def plan(*suites: str) -> dict[str, list]:  # type: ignore[type-arg]
    def variants(suite: str) -> list:  # type: ignore[type-arg]
        s = Scenario.model_validate(
            {
                "id": f"{suite}.a.b",
                "prd": ["x"],
                "status": "shipped",
                "suite": suite,
                "steps": [{"user": "hi"}],
                "expect": [{"ha_not_called": {}}],
            }
        )
        return expand_variants(s)

    return {suite: variants(suite) for suite in suites or ("demo",)}


# What execute() reads from a suite's RunContext.
NO_RECOVERIES = SimpleNamespace(recoveries=0)


class FakeLog(str):
    """A finished suite's log, as far as execute() looks: its status."""

    status = "success"


async def _eval_by_suite(task: str, **kwargs: Any) -> list[str]:
    return [FakeLog(f"log {task}")]


@pytest.mark.parametrize(
    ("start_error", "eval_error"),
    [(None, RuntimeError("inspect crashed")), (RuntimeError("docker exploded"), None)],
    ids=["eval-raises", "start-raises-something-else"],
)
async def test_execute_tears_down_and_propagates_anything_but_a_failed_start(
    tmp_path: Path, start_error: Exception | None, eval_error: Exception | None
) -> None:
    stacks: list[RecordingStack] = []

    def factory() -> RecordingStack:
        stacks.append(RecordingStack(start_error))
        return stacks[-1]

    async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        assert eval_error is not None
        raise eval_error

    with pytest.raises(RuntimeError, match=str(start_error or eval_error)):
        await execute(
            plan(),
            stack_factory=factory,
            make_ctx=lambda stack, variants: NO_RECOVERIES,  # type: ignore[arg-type,return-value]
            eval_fn=boom,
            log_dir=tmp_path,
            epochs=1,
            build_task_fn=lambda *a: "task",  # type: ignore[arg-type,return-value]
        )
    assert stacks[0].started == 1 and stacks[0].stopped == 1


async def test_execute_collects_logs_and_stack_meta(tmp_path: Path) -> None:
    async def fake_eval(task, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["max_samples"] == 1 and kwargs["retry_on_error"] == 1
        # Inside eval_async these would override the judge model's own GenerateConfig.
        assert not {"max_connections", "max_retries", "timeout"} & set(kwargs)
        return [FakeLog("log")]

    results = await execute(
        plan(),
        stack_factory=RecordingStack,
        make_ctx=lambda stack, variants: SimpleNamespace(recoveries=1),  # type: ignore[arg-type,return-value]
        eval_fn=fake_eval,
        log_dir=tmp_path,
        epochs=1,
        build_task_fn=lambda *a: "task",  # type: ignore[arg-type,return-value]
    )
    assert results.logs == ["log"] and results.unstarted == {}
    assert results.interrupted is None and results.problems == []
    # Recoveries are dead-container restarts, counted by the suite's RunContext.
    assert results.stacks == [
        {"suite": "demo", "boot_seconds": 1.0, "first_reply_ms": 100.0, "recoveries": 1}
    ]


async def test_a_stack_that_fails_to_start_costs_only_its_own_suite(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stacks = [
        RecordingStack(),
        RecordingStack(StackError("exited during boot\n--- docker logs ---\n  boom")),
    ]
    made = iter(stacks)

    results = await execute(
        plan("first", "second"),
        stack_factory=lambda: next(made),
        make_ctx=lambda stack, variants: NO_RECOVERIES,  # type: ignore[arg-type,return-value]
        eval_fn=_eval_by_suite,
        log_dir=tmp_path,
        epochs=1,
        build_task_fn=lambda suite, *a: suite,  # type: ignore[arg-type,return-value]
    )
    assert results.logs == ["log first"] and [m["suite"] for m in results.stacks] == ["first"]
    # The problem line holds the first line only; the log keeps the docker logs.
    assert results.unstarted == {
        "second": "suite second: stack failed to start: exited during boot"
    }
    assert "--- docker logs ---\n  boom" in caplog.text
    assert [(s.started, s.stopped) for s in stacks] == [(1, 1), (1, 1)]


async def test_suites_after_a_failed_start_still_run(tmp_path: Path) -> None:
    stacks = [RecordingStack(StackError("no port")), RecordingStack()]
    made = iter(stacks)

    results = await execute(
        plan("first", "second"),
        stack_factory=lambda: next(made),
        make_ctx=lambda stack, variants: NO_RECOVERIES,  # type: ignore[arg-type,return-value]
        eval_fn=_eval_by_suite,
        log_dir=tmp_path,
        epochs=1,
        build_task_fn=lambda suite, *a: suite,  # type: ignore[arg-type,return-value]
    )
    assert results.logs == ["log second"] and list(results.unstarted) == ["first"]
    assert [s.stopped for s in stacks] == [1, 1]


def _sleeping_task(started: asyncio.Event) -> Callable[..., Task]:
    """A real Inspect task whose solver signals *started*, then sleeps until cancelled."""

    @solver
    def sleepy() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            started.set()
            await asyncio.sleep(60)
            return state

        return solve

    def build(suite: str, variants: list[Any], ctx: Any, epochs: int) -> Task:
        samples = [Sample(input="x", id=v.sample_id) for v in variants]
        return Task(name=suite, dataset=samples, solver=sleepy())

    return build


async def test_ctrl_c_inside_inspect_stops_the_suite_loop_and_records_the_interruption(
    tmp_path: Path,
) -> None:
    # Inspect's eval_async absorbs the cancellation a Ctrl-C delivers: it returns no log
    # and leaves the task uncancelled. execute must still stop, not start the next suite.
    started = asyncio.Event()
    stacks = [RecordingStack(), RecordingStack()]
    made = iter(stacks)
    running = asyncio.create_task(
        execute(
            plan("first", "second"),
            stack_factory=lambda: next(made),
            make_ctx=lambda stack, variants: NO_RECOVERIES,  # type: ignore[arg-type,return-value]
            eval_fn=eval_async,
            log_dir=tmp_path,
            epochs=1,
            build_task_fn=_sleeping_task(started),
        )
    )
    await started.wait()
    running.cancel()
    results = await running

    assert results.interrupted == "first"
    assert results.problems == ["suite first: interrupted"]
    assert results.logs == [] and results.stacks == []
    # The interrupted suite's stack was torn down; the second suite's never started.
    assert [(s.started, s.stopped) for s in stacks] == [(1, 1), (0, 0)]


async def test_ctrl_c_while_a_stack_boots_is_an_interruption_too(tmp_path: Path) -> None:
    booting = asyncio.Event()

    class SlowStack(RecordingStack):
        async def start(self) -> None:
            self.started += 1
            booting.set()
            await asyncio.sleep(60)

    stacks = [SlowStack(), RecordingStack()]
    made = iter(stacks)
    running = asyncio.create_task(
        execute(
            plan("first", "second"),
            stack_factory=lambda: next(made),
            make_ctx=lambda stack, variants: NO_RECOVERIES,  # type: ignore[arg-type,return-value]
            eval_fn=_eval_by_suite,
            log_dir=tmp_path,
            epochs=1,
            build_task_fn=lambda suite, *a: suite,  # type: ignore[arg-type,return-value]
        )
    )
    await booting.wait()
    running.cancel()
    results = await running

    assert results.interrupted == "first" and results.problems == ["suite first: interrupted"]
    assert [(s.started, s.stopped) for s in stacks] == [(1, 1), (0, 0)]
    # The cancellation was handled here, so the task carries on normally afterwards.
    assert not running.cancelled()


@pytest.mark.parametrize(
    "returned",
    [[], [SimpleNamespace(status="cancelled")]],
    ids=["no-log", "cancelled-log"],
)
async def test_a_suite_without_a_finished_log_was_interrupted(
    tmp_path: Path, returned: list[Any]
) -> None:
    async def eval_fn(task: Any, **kwargs: Any) -> list[Any]:
        return returned

    results = await execute(
        plan("first", "second"),
        stack_factory=RecordingStack,
        make_ctx=lambda stack, variants: NO_RECOVERIES,  # type: ignore[arg-type,return-value]
        eval_fn=eval_fn,
        log_dir=tmp_path,
        epochs=1,
        build_task_fn=lambda suite, *a: suite,  # type: ignore[arg-type,return-value]
    )
    assert results.interrupted == "first" and results.logs == []


DIGESTS = {"tone": "d-tone", "answered": "d-answered"}


def _report(model: str, digests: dict[str, str] = DIGESTS) -> CalibrationReport:
    result = CategoryResult(agreement=0.9, n=10, disagreements=["t-1"], unparseable=[])
    low = CategoryResult(agreement=0.5, n=10, disagreements=[], unparseable=[])
    return CalibrationReport(
        model=model, categories={"tone": result, "answered": low}, digests=digests
    )


def test_calibration_for_uses_only_the_runs_own_model(caplog: pytest.LogCaptureFixture) -> None:
    assert calibration_for(_report("judge-m"), "judge-m", DIGESTS) == (
        {"tone": 0.9, "answered": 0.5},
        {"tone"},
    )
    assert not caplog.records
    with caplog.at_level(logging.WARNING, logger="evals.harness.orchestrate"):
        assert calibration_for(_report("another-m"), "judge-m", DIGESTS) == ({}, set())
        assert calibration_for(None, "judge-m", DIGESTS) == ({}, set())
    mismatch, missing = (r.getMessage() for r in caplog.records)
    assert "(the saved one is for another-m)" in mismatch and "saved one" not in missing
    assert all("alfred evals calibrate --model judge-m" in m for m in (mismatch, missing))


@pytest.mark.parametrize(
    ("saved", "stale"),
    [
        ({"tone": "d-tone-before", "answered": "d-answered"}, "tone"),
        # Saved before digests existed: nothing proves what it measured.
        ({}, "answered, tone"),
    ],
    ids=["edited", "no-digests"],
)
def test_a_category_whose_items_changed_since_calibration_is_uncalibrated(
    caplog: pytest.LogCaptureFixture, saved: dict[str, str], stale: str
) -> None:
    with caplog.at_level(logging.WARNING, logger="evals.harness.orchestrate"):
        calibration, trusted = calibration_for(_report("judge-m", saved), "judge-m", DIGESTS)
    kept = {"answered": 0.5} if stale == "tone" else {}
    assert (calibration, trusted) == (kept, set())
    [warning] = (r.getMessage() for r in caplog.records)
    assert f"changed since it was measured: {stale}" in warning
    assert "alfred evals calibrate --model judge-m" in warning


def test_a_category_added_or_removed_since_calibration_is_uncalibrated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    current = {"tone": "d-tone", "relevance": "d-relevance"}  # answered's file is gone
    with caplog.at_level(logging.WARNING, logger="evals.harness.orchestrate"):
        assert calibration_for(_report("judge-m"), "judge-m", current) == (
            {"tone": 0.9},
            {"tone"},
        )
    [warning] = (r.getMessage() for r in caplog.records)
    assert "changed since it was measured: answered, relevance" in warning


def test_read_calibration_reads_a_saved_report_or_none(tmp_path: Path) -> None:
    path = tmp_path / "calibration.json"
    assert read_calibration(path, "judge-m") is None
    path.write_text(_report("judge-m").model_dump_json(), encoding="utf-8")
    report = read_calibration(path, "judge-m")
    assert report is not None and report.model == "judge-m"


def _unreadable(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path.write_text("{}", encoding="utf-8")

    def refuse(self: Path, *args: Any, **kwargs: Any) -> str:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", refuse)


@pytest.mark.parametrize(
    ("spoil", "first_line"),
    [
        (lambda p, mp: p.write_text("{not json", encoding="utf-8"), "Expecting property name"),
        (
            lambda p, mp: p.write_text('{"model": 3}', encoding="utf-8"),
            "validation error",
        ),
        (lambda p, mp: p.write_bytes(b"\xff\xfe"), "can't decode"),
        (_unreadable, "Permission denied"),
    ],
    ids=["bad-json", "failed-validation", "not-utf8", "os-error"],
)
def test_an_unreadable_calibration_is_a_preflight_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spoil: Any, first_line: str
) -> None:
    path = tmp_path / "calibration.json"
    spoil(path, monkeypatch)
    with pytest.raises(PreflightError) as err:
        read_calibration(path, "judge-m")
    message = str(err.value)
    assert message.startswith(f"judge calibration at {path} is unreadable (")
    assert message.endswith(") — re-run `alfred evals calibrate --model judge-m`")
    assert first_line in message and "\n" not in message


def _alfredctl() -> Path:
    return Path(sys.executable).parent / "alfredctl"


def test_a_failed_build_streams_its_output_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append((cmd, kwargs))
        raise subprocess.CalledProcessError(2, cmd)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(StackError) as err:
        _build_image(tmp_path / "home-service")
    assert str(err.value) == "image build failed (exit 2) — see the build output above"
    ((cmd, kwargs),) = seen
    assert cmd == [str(_alfredctl()), "build", "--runtime", "docker"]
    assert kwargs["env"]["ALFRED_HOME_SERVICE_DIR"] == str(tmp_path / "home-service")
    # The build prints to the terminal as it goes, and takes as long as it takes.
    assert not {"capture_output", "stdout", "stderr", "timeout"} & set(kwargs)


def test_a_missing_alfredctl_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(StackError, match="No such file or directory") as err:
        _build_image(tmp_path)
    assert str(_alfredctl()) in str(err.value)


def _golden(root: Path, suite: str, name: str, **fields: Any) -> None:
    body = {
        "id": f"{suite}.demo.{name}",
        "prd": ["x"],
        "status": "shipped",
        "steps": [{"wait": 0.01}],
        "expect": [{"ha_not_called": {}}],
        **fields,
    }
    (root / suite).mkdir(parents=True, exist_ok=True)
    (root / suite / f"{name}.yaml").write_text(json.dumps(body), encoding="utf-8")


def _options(log_root: Path, **fields: Any) -> RunOptions:
    base: dict[str, Any] = {
        "suites": [],
        "tags": [],
        "include_pending": False,
        "epochs": 1,
        "model": "judge-m",
        "vllm_url": "http://vllm.test/v1",
        "embed_url": "http://embed.test",
        "embed_model": "embed-m",
        "home_service": log_root / "home-service",
        "allow_stale_home_service": False,
        "build": True,
        "keep": False,
        "log_root": log_root,
        "display": "none",
        # Any free port: a test must not hold the real defaults.
        "fake_ha_port": 0,
        "proxy_port": 0,
    }
    return RunOptions(**{**base, **fields})


def _suites_at(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(orchestrate, "load_suites", functools.partial(load_suites, root=root))


def test_build_plan_selects_by_tag_and_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _golden(tmp_path, "demo", "tagged", tags=["lights"])
    _golden(tmp_path, "demo", "untagged")
    _golden(tmp_path, "demo", "pending", tags=["lights"], status="pending")
    _golden(tmp_path, "other", "untagged")
    _suites_at(monkeypatch, tmp_path)

    chosen = build_plan(_options(tmp_path, tags=["lights"]))
    assert {s: [v.sample_id for v in vs] for s, vs in chosen.items()} == {
        "demo": ["demo.demo.tagged"]
    }
    chosen = build_plan(_options(tmp_path, tags=["lights"], include_pending=True))
    assert [v.sample_id for v in chosen["demo"]] == ["demo.demo.pending", "demo.demo.tagged"]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"tags": ["nothing-has-this"]}, "no goldens match"),
        ({"world": "mansion"}, "only the 'apartment' world"),
    ],
    ids=["nothing-selected", "unserved-world"],
)
def test_build_plan_refuses(
    fields: dict[str, Any], message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    golden_fields = {k: v for k, v in fields.items() if k != "tags"}
    _golden(tmp_path, "demo", "one", **golden_fields)
    _suites_at(monkeypatch, tmp_path)
    tags = fields.get("tags", [])
    with pytest.raises(ScenarioError, match=message):
        build_plan(_options(tmp_path, tags=tags))


class FakeStack:
    """A stack that never needs docker: the goldens used here send nothing."""

    def __init__(self, cfg: Any, start_error: StackError | None = None) -> None:
        self.cfg = cfg
        self.name = "alfred-eval-fake"
        self.boot_seconds = 2.0
        self.first_reply_ms = 300.0
        self.restarts = 0
        self.stopped = False
        self._start_error = start_error

    async def start(self) -> None:
        if self._start_error is not None:
            raise self._start_error

    async def stop(self) -> None:
        self.stopped = True

    async def alive(self) -> bool:
        return True

    async def restart(self) -> None:
        self.restarts += 1

    async def send(self, request: Any, timeout_s: float) -> Any:
        raise AssertionError("the goldens have no user step")


def _preflight_passes(monkeypatch: pytest.MonkeyPatch, order: list[str]) -> None:
    """Every preflight check passes and records itself in *order*; the build only records."""

    async def check_models(client: object, base_url: str, model: str) -> None:
        order.append(f"models {base_url} {model}")

    def check_home_service(path: Path, *, allow_stale: bool) -> str:
        order.append("home-service")
        return "def5678"

    def home_service_commit(path: Path) -> str:
        order.append("home-service commit")
        return "def5678"

    def alfred_commit(repo: Path) -> str:
        order.append("alfred commit")
        return "abc1234+dirty"

    real_read_calibration = orchestrate.read_calibration

    def read_calibration(path: Path, model: str) -> CalibrationReport | None:
        order.append(f"calibration {path.name}")
        return real_read_calibration(path, model)

    def gateway() -> str:
        order.append("gateway")
        return "127.0.0.1"

    async def probe(ports: dict[str, int], *, gateway: str) -> None:
        assert all(port > 0 for port in ports.values())  # the ports the fakes really got
        order.append(f"probe {sorted(ports)} via {gateway}")

    monkeypatch.setattr(orchestrate, "check_models", check_models)
    monkeypatch.setattr(orchestrate, "probe_host_ports", probe)
    monkeypatch.setattr(orchestrate, "check_home_service", check_home_service)
    monkeypatch.setattr(orchestrate, "home_service_commit", home_service_commit)
    monkeypatch.setattr(orchestrate, "alfred_commit", alfred_commit)
    monkeypatch.setattr(orchestrate, "read_calibration", read_calibration)
    monkeypatch.setattr(orchestrate, "docker_bridge_gateway", gateway)
    monkeypatch.setattr(orchestrate, "_build_image", lambda hs: order.append(f"build {hs.name}"))


@pytest.mark.parametrize(
    ("build", "checked", "built", "commit", "hs_commit"),
    [
        (True, "home-service", ["build home-service"], "abc1234+dirty", "def5678"),
        # The image holds whatever home-service it was built with: the checkout is only
        # described, never refused, and both commits say the image was not rebuilt.
        (
            False,
            "home-service commit",
            [],
            "abc1234+dirty (image not rebuilt)",
            "def5678 (image not rebuilt)",
        ),
    ],
    ids=["build", "no-build"],
)
async def test_run_suites_preflights_before_the_build_and_reports_whatever_ran(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    build: bool,
    checked: str,
    built: list[str],
    commit: str,
    hs_commit: str,
) -> None:
    # Inspect's display is process-global: unset it here, and restore it afterwards.
    # Neither eval_async's own fallback nor the env var left here is the display asked for.
    monkeypatch.setattr(inspect_display, "_display_type", None)
    monkeypatch.setenv("INSPECT_DISPLAY", "plain")
    # Isolated, so its stack restarts before the sample: a restart, not a recovery.
    _golden(tmp_path / "suites", "demo", "quiet", isolated=True)
    _golden(tmp_path / "suites", "zzz", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    calibration_file = tmp_path / "calibration.json"
    calibration_file.write_text(_report("another-m").model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(orchestrate, "CALIBRATION_FILE", calibration_file)
    order: list[str] = []
    _preflight_passes(monkeypatch, order)
    stacks: list[FakeStack] = []
    problems_asked: list[int] = []

    def judge_model(model: str, base_url: str) -> Model:
        return get_model("mockllm/model", memoize=False)

    def stack(cfg: Any, *, fake_ha: Any, proxy: Any) -> FakeStack:
        # The second suite's stack never comes up.
        stacks.append(FakeStack(cfg, StackError("no port") if stacks else None))
        return stacks[-1]

    def log_problems(logs: list[EvalLog], epochs: int) -> list[str]:
        problems_asked.append(epochs)
        return ["demo: a problem with the run"]

    monkeypatch.setattr(orchestrate, "make_judge_model", judge_model)
    monkeypatch.setattr(orchestrate, "Stack", stack)
    monkeypatch.setattr(orchestrate, "log_problems", log_problems)

    outcome = await run_suites(_options(tmp_path / "logs", build=build, display="none"))

    assert display_type() == "none"
    assert order == [
        "models http://vllm.test/v1 judge-m",
        "models http://embed.test/v1 embed-m",
        checked,
        "alfred commit",
        "calibration calibration.json",
        "gateway",
        *built,
        "probe ['LLM proxy', 'fake HA'] via 127.0.0.1",
    ]
    run_dir = outcome.run_dir
    assert run_dir.parent == tmp_path / "logs" and outcome.unstarted == ["zzz"]
    assert [s.stopped for s in stacks] == [True, True]
    assert stacks[0].cfg.work_dir == run_dir / "data"
    card = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    meta = card["meta"]
    assert meta["alfred_commit"] == commit and meta["home_service_commit"] == hs_commit
    # The saved calibration was measured on another model, so none of it applies.
    assert meta["calibration"] == {} and meta["trusted"] == []
    assert meta["problems"] == [
        "suite zzz: stack failed to start: no port",
        "demo: a problem with the run",
    ]
    assert problems_asked == [1]
    assert stacks[0].restarts == 1
    assert meta["stacks"] == [
        {"suite": "demo", "boot_seconds": 2.0, "first_reply_ms": 300.0, "recoveries": 0}
    ]
    assert [(g["scenario_id"], g["passes"], g["runs"]) for g in card["goldens"]] == [
        ("demo.demo.quiet", 1, 1)
    ]
    printed = capsys.readouterr().out
    assert "# Alfred eval scorecard" in printed
    assert "suite zzz: stack failed to start: no port" in printed


async def test_an_interrupted_run_writes_the_scorecard_for_what_finished_then_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _golden(tmp_path / "suites", "demo", "quiet")
    _golden(tmp_path / "suites", "zzz", "quiet")
    _golden(tmp_path / "suites", "zzzz", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    monkeypatch.setattr(orchestrate, "CALIBRATION_FILE", tmp_path / "calibration.json")
    _preflight_passes(monkeypatch, [])
    monkeypatch.setattr(
        orchestrate, "make_judge_model", lambda m, u: get_model("mockllm/model", memoize=False)
    )
    stacks: list[FakeStack] = []

    def stack(cfg: Any, *, fake_ha: Any, proxy: Any) -> FakeStack:
        stacks.append(FakeStack(cfg))
        return stacks[-1]

    monkeypatch.setattr(orchestrate, "Stack", stack)
    real_eval = orchestrate.eval_async

    async def ctrl_c_in_zzz(task: Task, **kwargs: Any) -> list[EvalLog]:
        if task.name == "zzz":
            return []  # what eval_async returns once a Ctrl-C has cancelled it
        return await real_eval(task, **kwargs)

    monkeypatch.setattr(orchestrate, "eval_async", ctrl_c_in_zzz)

    with pytest.raises(RunCancelled) as err:
        await run_suites(_options(tmp_path / "logs"))

    run_dir = err.value.run_dir
    card = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert card["meta"]["problems"] == ["suite zzz: interrupted"]
    assert [g["scenario_id"] for g in card["goldens"]] == ["demo.demo.quiet"]
    assert [m["suite"] for m in card["meta"]["stacks"]] == ["demo"]
    assert "- suite zzz: interrupted" in capsys.readouterr().out
    # Both stacks that started were torn down; the suite after the interrupted one never ran.
    assert [s.stopped for s in stacks] == [True, True]


async def test_run_suites_normalises_the_server_urls_before_using_any(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _golden(tmp_path / "suites", "demo", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    monkeypatch.setattr(orchestrate, "CALIBRATION_FILE", tmp_path / "calibration.json")
    order: list[str] = []
    _preflight_passes(monkeypatch, order)
    judged: list[str] = []

    def judge_model(model: str, url: str) -> Model:
        judged.append(url)
        return get_model("mockllm/model", memoize=False)

    seen: dict[str, str] = {}

    def stack(cfg: Any, *, fake_ha: Any, proxy: Any) -> FakeStack:
        seen.update(vllm=cfg.vllm_url, embed=cfg.embed_url, upstream=proxy.upstream)
        return FakeStack(cfg)

    monkeypatch.setattr(orchestrate, "make_judge_model", judge_model)
    monkeypatch.setattr(orchestrate, "Stack", stack)
    opts = _options(
        tmp_path / "logs", vllm_url="http://vllm.test/v1/", embed_url="http://embed.test//"
    )
    await run_suites(opts)
    assert order[:2] == [
        "models http://vllm.test/v1 judge-m",
        "models http://embed.test/v1 embed-m",
    ]
    assert judged == ["http://vllm.test/v1"]
    # Not /v1/: the proxy would otherwise forward to /v1/v1/chat/completions.
    assert seen == {
        "vllm": "http://vllm.test/v1",
        "embed": "http://embed.test",
        "upstream": "http://vllm.test",
    }


@pytest.mark.parametrize("edited", [False, True], ids=["current", "tone-edited"])
async def test_run_suites_trusts_only_a_calibration_of_the_items_as_they_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edited: bool
) -> None:
    _golden(tmp_path / "suites", "demo", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    current = calibration_digests(load_calibration_sets())
    saved = {c: current[c] for c in ("tone", "answered")}
    if edited:
        saved["tone"] = "measured-on-other-items"
    calibration_file = tmp_path / "calibration.json"
    calibration_file.write_text(_report("judge-m", saved).model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(orchestrate, "CALIBRATION_FILE", calibration_file)
    _preflight_passes(monkeypatch, [])
    monkeypatch.setattr(
        orchestrate, "make_judge_model", lambda m, u: get_model("mockllm/model", memoize=False)
    )
    monkeypatch.setattr(orchestrate, "Stack", lambda cfg, **_: FakeStack(cfg))

    outcome = await run_suites(_options(tmp_path / "logs"))

    meta = json.loads((outcome.run_dir / "report.json").read_text(encoding="utf-8"))["meta"]
    if edited:
        assert (meta["calibration"], meta["trusted"]) == ({"answered": 0.5}, [])
    else:
        assert (meta["calibration"], meta["trusted"]) == ({"tone": 0.9, "answered": 0.5}, ["tone"])


@pytest.mark.parametrize(
    ("field", "option"), [("vllm_url", "--vllm-url"), ("embed_url", "--embed-url")]
)
async def test_a_malformed_server_url_is_a_preflight_error_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, option: str
) -> None:
    _golden(tmp_path / "suites", "demo", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    order: list[str] = []
    _preflight_passes(monkeypatch, order)
    with pytest.raises(PreflightError, match=f"^{option} 'http://localhost:80000'"):
        await run_suites(_options(tmp_path / "logs", **{field: "http://localhost:80000"}))
    assert order == [] and not (tmp_path / "logs").exists()


def _models_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def unreachable(client: object, base_url: str, model: str) -> None:
        raise PreflightError(f"{base_url} is not reachable")

    monkeypatch.setattr(orchestrate, "check_models", unreachable)


def _calibration_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corrupt = tmp_path / "calibration.json"
    corrupt.write_text("{truncated", encoding="utf-8")
    monkeypatch.setattr(orchestrate, "CALIBRATION_FILE", corrupt)


def _calibration_set_broken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sets = tmp_path / "judge_calibration"
    sets.mkdir()
    (sets / "tone.yaml").write_text("category: tone\nitems: []\n", encoding="utf-8")
    monkeypatch.setattr(orchestrate, "CALIBRATION_DIR", sets)


@pytest.mark.parametrize(
    ("fail", "message"),
    [
        (_models_unreachable, "not reachable"),
        (_calibration_corrupt, r"judge calibration at .* is unreadable"),
        (_calibration_set_broken, r"tone\.yaml: items: List should have at least 1 item"),
    ],
    ids=["models-unreachable", "calibration-corrupt", "calibration-set-broken"],
)
async def test_a_failed_preflight_never_builds_or_writes_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: Any, message: str
) -> None:
    _golden(tmp_path / "suites", "demo", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    order: list[str] = []
    _preflight_passes(monkeypatch, order)
    fail(tmp_path, monkeypatch)
    with pytest.raises(PreflightError, match=message):
        await run_suites(_options(tmp_path / "logs"))
    assert not [step for step in order if step.startswith(("gateway", "build", "probe"))]
    assert not (tmp_path / "logs").exists()


_IN_USE_HINT = (
    " — another `alfred evals run` may be using it; runs on one branch share a container "
    "name and cannot overlap"
)


class FailingFakeHA(FakeHA):
    error = (errno.EADDRINUSE, "Address already in use")

    async def start(self) -> None:
        raise OSError(*self.error)


class UnassignableFakeHA(FailingFakeHA):
    error = (errno.EADDRNOTAVAIL, "Cannot assign requested address")


class FailingProxy(LlmProxy):
    async def start(self) -> None:
        await super().start()  # gets as far as binding, then fails
        raise OSError(errno.EADDRINUSE, "Address already in use")


@pytest.mark.parametrize(
    ("fake_ha_cls", "proxy_cls", "ports", "says"),
    [
        # The failing fake HA never binds, so its fixed port is safe to ask for here.
        (
            FailingFakeHA,
            LlmProxy,
            {"fake_ha_port": 18123},
            "could not start the fake HA on 127.0.0.1:18123 (choose another with "
            f"--fake-ha-port): [Errno 98] Address already in use{_IN_USE_HINT}",
        ),
        (
            FakeHA,
            FailingProxy,
            {},
            "could not start the LLM proxy on 127.0.0.1 (any free port): "
            f"[Errno 98] Address already in use{_IN_USE_HINT}",
        ),
        # Only a busy port can be another run's.
        (
            UnassignableFakeHA,
            LlmProxy,
            {"fake_ha_port": 18123},
            "could not start the fake HA on 127.0.0.1:18123 (choose another with "
            "--fake-ha-port): [Errno 99] Cannot assign requested address",
        ),
    ],
    ids=["fake-ha-in-use", "proxy-in-use", "fake-ha-unassignable"],
)
async def test_a_fake_that_cannot_start_is_a_stack_error_and_nothing_is_left_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_ha_cls: type[FakeHA],
    proxy_cls: type[LlmProxy],
    ports: dict[str, int],
    says: str,
) -> None:
    _golden(tmp_path / "suites", "demo", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    monkeypatch.setattr(orchestrate, "CALIBRATION_FILE", tmp_path / "calibration.json")
    order: list[str] = []
    _preflight_passes(monkeypatch, order)
    made: list[FakeHA | LlmProxy] = []

    def recorded(cls: type[Any]) -> Any:
        def make(*args: Any, **kwargs: Any) -> Any:
            made.append(cls(*args, **kwargs))
            return made[-1]

        return make

    monkeypatch.setattr(orchestrate, "FakeHA", recorded(fake_ha_cls))
    monkeypatch.setattr(orchestrate, "LlmProxy", recorded(proxy_cls))
    stacks: list[object] = []
    monkeypatch.setattr(orchestrate, "Stack", lambda *a, **kw: stacks.append(a))

    with pytest.raises(StackError) as err:
        await run_suites(_options(tmp_path / "logs", display="none", **ports))

    assert str(err.value) == says
    # A busy port stops the run before the build retags the image another run is using.
    assert not [step for step in order if step.startswith(("build", "probe"))]
    assert stacks == []  # no suite ran
    assert not (tmp_path / "logs").exists()  # and no empty run dir is left behind
    fake_ha, proxy = made
    assert isinstance(fake_ha, FakeHA) and isinstance(proxy, LlmProxy)
    # Whatever started was stopped.
    assert fake_ha._server is None
    assert proxy._runner is None and proxy._client is None


def _recording_fakes(
    monkeypatch: pytest.MonkeyPatch, order: list[str]
) -> tuple[dict[str, int], list[Any]]:
    """The port each fake was asked for, and the fakes; each records its start in *order*
    and really binds any free port."""
    asked: dict[str, int] = {}
    made: list[Any] = []

    def recorded(cls: type[Any]) -> Any:
        class Recorded(cls):  # type: ignore[valid-type,misc]
            async def start(self) -> None:
                await super().start()
                order.append(f"{cls.__name__} start")

        def make(*args: Any, port: int, **kwargs: Any) -> Any:
            asked[cls.__name__] = port
            made.append(Recorded(*args, port=0, **kwargs))
            return made[-1]

        return make

    monkeypatch.setattr(orchestrate, "FakeHA", recorded(FakeHA))
    monkeypatch.setattr(orchestrate, "LlmProxy", recorded(LlmProxy))
    return asked, made


async def test_run_suites_binds_the_fakes_then_builds_then_probes_before_any_stack_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _golden(tmp_path / "suites", "demo", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    monkeypatch.setattr(orchestrate, "CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(
        orchestrate, "make_judge_model", lambda m, u: get_model("mockllm/model", memoize=False)
    )
    order: list[str] = []
    _preflight_passes(monkeypatch, order)
    asked, _ = _recording_fakes(monkeypatch, order)

    class OrderedStack(FakeStack):
        async def start(self) -> None:
            order.append("stack start")

    monkeypatch.setattr(orchestrate, "Stack", lambda cfg, **kw: OrderedStack(cfg))

    await run_suites(_options(tmp_path / "logs", build=True, fake_ha_port=18123, proxy_port=18100))

    assert asked == {"FakeHA": 18123, "LlmProxy": 18100}
    # The fakes bind before the build: a concurrent run fails on the busy port before it
    # retags alfred:<branch>. The probe needs the image, so it follows the build.
    assert order[order.index("gateway") + 1 :] == [
        "FakeHA start",
        "LlmProxy start",
        "build home-service",
        "probe ['LLM proxy', 'fake HA'] via 127.0.0.1",
        "stack start",
    ]


async def test_a_failed_build_stops_the_fakes_it_started_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _golden(tmp_path / "suites", "demo", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    monkeypatch.setattr(orchestrate, "CALIBRATION_FILE", tmp_path / "calibration.json")
    order: list[str] = []
    _preflight_passes(monkeypatch, order)
    _, made = _recording_fakes(monkeypatch, order)
    failed = StackError("image build failed (exit 2) — see the build output above")

    def build(home_service: Path) -> None:
        order.append("build")
        raise failed

    monkeypatch.setattr(orchestrate, "_build_image", build)
    stacks: list[object] = []
    monkeypatch.setattr(orchestrate, "Stack", lambda *a, **kw: stacks.append(a))

    with pytest.raises(StackError) as err:
        await run_suites(_options(tmp_path / "logs", build=True))

    assert err.value is failed
    assert order[order.index("gateway") + 1 :] == ["FakeHA start", "LlmProxy start", "build"]
    assert stacks == [] and not (tmp_path / "logs").exists()
    fake_ha, proxy = made
    assert fake_ha._server is None and proxy._runner is None and proxy._client is None


async def test_a_failed_probe_stops_the_run_before_any_suite_and_stops_the_fakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _golden(tmp_path / "suites", "demo", "quiet")
    _suites_at(monkeypatch, tmp_path / "suites")
    monkeypatch.setattr(orchestrate, "CALIBRATION_FILE", tmp_path / "calibration.json")
    _preflight_passes(monkeypatch, [])
    _, made = _recording_fakes(monkeypatch, [])
    blocked = StackError("a container cannot reach the fake HA … (see docs/evals.md#host-firewall)")

    async def probe(ports: dict[str, int], *, gateway: str) -> None:
        raise blocked

    monkeypatch.setattr(orchestrate, "probe_host_ports", probe)
    stacks: list[object] = []
    monkeypatch.setattr(orchestrate, "Stack", lambda *a, **kw: stacks.append(a))

    with pytest.raises(StackError) as err:
        await run_suites(_options(tmp_path / "logs"))

    assert err.value is blocked
    assert stacks == []  # no suite's stack was made, let alone started
    assert not (tmp_path / "logs").exists()
    fake_ha, proxy = made
    assert fake_ha._server is None and proxy._runner is None
