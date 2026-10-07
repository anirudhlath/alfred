"""`alfred evals run`: preflight, build, then one stack and one Inspect task per suite."""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from inspect_ai import eval_async

from evals.harness.display import use_display
from evals.harness.driver import PlayContext
from evals.harness.fake_ha import FakeHA
from evals.harness.judge import (
    CALIBRATION_DIR,
    CALIBRATION_FILE,
    Judge,
    calibration_digests,
    load_calibration_sets,
    load_report,
    make_judge_model,
)
from evals.harness.net import docker_bridge_gateway
from evals.harness.preflight import (
    PreflightError,
    alfred_commit,
    base_url,
    check_home_service,
    check_models,
    home_service_commit,
)
from evals.harness.proxy import LlmProxy
from evals.harness.report import (
    RunMeta,
    log_problems,
    render_markdown,
    runs_from_logs,
    summarize,
    write_report,
)
from evals.harness.scenario import ScenarioError, expand_variants, load_suites, select
from evals.harness.stack import Stack, StackConfig, StackError, probe_host_ports
from evals.harness.tasks import RunContext, build_task
from evals.harness.world import load_world

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from inspect_ai import Task
    from inspect_ai.log import EvalLog

    from evals.harness.display import Display
    from evals.harness.judge import CalibrationReport
    from evals.harness.scenario import ScenarioVariant

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = REPO_ROOT / "evals" / "logs"
# With --no-build the scorecard cannot know what the image holds; both commits say so.
NOT_REBUILT = " (image not rebuilt)"


@dataclass(frozen=True)
class RunOptions:
    suites: list[str]
    tags: list[str]
    include_pending: bool
    epochs: int
    model: str
    vllm_url: str
    embed_url: str
    embed_model: str
    home_service: Path
    allow_stale_home_service: bool
    build: bool
    keep: bool
    log_root: Path
    display: Display
    fake_ha_port: int  # 0: any free port
    proxy_port: int  # 0: any free port


@dataclass(frozen=True)
class RunOutcome:
    run_dir: Path
    unstarted: list[str]  # suites whose stack failed to start; the run exits 1


class RunCancelled(RuntimeError):  # noqa: N818 — a cancellation, not an error
    """A Ctrl-C stopped the run. Raised after the scorecard for what finished is written;
    the CLI exits 130."""

    def __init__(self, run_dir: Path) -> None:
        super().__init__(f"interrupted; the scorecard covers the suites that finished: {run_dir}")
        self.run_dir = run_dir


@dataclass
class SuiteResults:
    """What ``execute`` ran: each finished suite's logs and stack numbers, and why the
    others did not finish."""

    logs: list[EvalLog] = field(default_factory=list)
    stacks: list[dict[str, Any]] = field(default_factory=list)
    unstarted: dict[str, str] = field(default_factory=dict)  # suite → its problem line
    interrupted: str | None = None  # the suite a Ctrl-C stopped; no later suite ran

    @property
    def problems(self) -> list[str]:
        lines = list(self.unstarted.values())
        if self.interrupted is not None:
            lines.append(f"suite {self.interrupted}: interrupted")
        return lines


def _finished(logs: Sequence[EvalLog]) -> bool:
    """Whether Inspect ran the suite to the end. Inspect absorbs the cancellation a Ctrl-C
    delivers: ``eval_async`` then returns no log, or a ``cancelled`` one, and returns
    normally."""
    return bool(logs) and not any(log.status == "cancelled" for log in logs)


def build_plan(opts: RunOptions) -> dict[str, list[ScenarioVariant]]:
    plan: dict[str, list[ScenarioVariant]] = {}
    for suite, scenarios in load_suites(opts.suites or None).items():
        chosen = select(scenarios, tags=opts.tags, include_pending=opts.include_pending)
        variants = [v for s in chosen for v in expand_variants(s)]
        if variants:
            plan[suite] = variants
    if not plan:
        raise ScenarioError("no goldens match those suites, tags and statuses")
    worlds = {v.scenario.world for vs in plan.values() for v in vs}
    if worlds != {"apartment"}:
        raise ScenarioError(
            f"slice 1 serves only the 'apartment' world; goldens ask for {sorted(worlds)}"
        )
    return plan


async def execute(
    plan: dict[str, list[ScenarioVariant]],
    *,
    stack_factory: Callable[[], Any],
    make_ctx: Callable[[Any, list[ScenarioVariant]], RunContext],
    eval_fn: Callable[..., Awaitable[list[EvalLog]]],
    log_dir: Path,
    epochs: int,
    build_task_fn: Callable[[str, list[ScenarioVariant], RunContext, int], Task] = build_task,
) -> SuiteResults:
    """Run each suite on its own stack. A suite whose stack fails to start gets a problem
    line and the next suite still runs; a Ctrl-C stops the loop at the suite it hit."""
    results = SuiteResults()
    for suite, variants in plan.items():
        stack = stack_factory()
        try:
            try:
                await stack.start()
            except StackError as exc:
                # The full text (docker logs included) goes to the log; the scorecard's
                # problem line keeps the first line.
                logger.error("suite %s: stack failed to start: %s", suite, exc)
                lines = str(exc).strip().splitlines()
                why = lines[0].strip() if lines else type(exc).__name__
                results.unstarted[suite] = f"suite {suite}: stack failed to start: {why}"
                continue
            ctx = make_ctx(stack, variants)
            task = build_task_fn(suite, variants, ctx, epochs)
            # No max_connections, max_retries or timeout here: inside eval_async they
            # would override the judge model's own GenerateConfig.
            logs = await eval_fn(
                task,
                model="mockllm/model",
                log_dir=str(log_dir),
                max_samples=1,
                retry_on_error=1,
                fail_on_error=False,
            )
            if not _finished(logs):
                results.interrupted = suite
                break
            results.logs += logs
            results.stacks.append(
                {
                    "suite": suite,
                    "boot_seconds": stack.boot_seconds,
                    "first_reply_ms": stack.first_reply_ms,
                    "recoveries": ctx.recoveries,
                }
            )
        except asyncio.CancelledError:
            # A Ctrl-C outside Inspect (while the stack boots) is the same interruption as
            # one Inspect absorbed: handled here, so the scorecard is still written.
            if (current := asyncio.current_task()) is not None:
                current.uncancel()
            results.interrupted = suite
            break
        finally:
            await stack.stop()
    return results


def calibration_for(
    report: CalibrationReport | None, model: str, digests: Mapping[str, str]
) -> tuple[dict[str, float], set[str]]:
    """The judge calibration a run on *model* reports, and the categories it trusts.

    A calibration measured on another model counts as missing: its agreements say
    nothing about this judge. So does a category measured on other items than its file
    holds now (*digests*, from ``calibration_digests``): edited, added or removed since.
    """
    if report is None or report.model != model:
        logger.warning(
            "no judge calibration for %s%s — every judge check is untrusted; "
            "run `alfred evals calibrate --model %s`",
            model,
            "" if report is None else f" (the saved one is for {report.model})",
            model,
        )
        return {}, set()
    changed = sorted(
        c
        for c in report.categories.keys() | digests.keys()
        if report.digests.get(c) != digests.get(c)
    )
    if changed:
        logger.warning(
            "the judge calibration for %s is stale for the categories whose hand-labelled "
            "items changed since it was measured: %s — their judge checks are untrusted; "
            "run `alfred evals calibrate --model %s`",
            model,
            ", ".join(changed),
            model,
        )
    current = {c: r.agreement for c, r in report.categories.items() if c not in changed}
    return current, report.trusted() - set(changed)


def read_calibration(path: Path, model: str) -> CalibrationReport | None:
    """The saved judge calibration, or None when there is none. Unreadable is a preflight
    error: running on would quietly score every judge check as untrusted."""
    try:
        return load_report(path)
    except (OSError, ValueError) as exc:  # ValueError covers bad JSON, UTF-8 and validation
        lines = str(exc).strip().splitlines()
        first = lines[0] if lines else type(exc).__name__
        raise PreflightError(
            f"judge calibration at {path} is unreadable ({first}) — "
            f"re-run `alfred evals calibrate --model {model}`"
        ) from exc


def _build_image(home_service: Path) -> None:
    """Build the image with *home_service* bundled. The build prints as it goes."""
    alfredctl = Path(sys.executable).parent / "alfredctl"
    env = {**os.environ, "ALFRED_HOME_SERVICE_DIR": str(home_service)}
    try:
        subprocess.run([str(alfredctl), "build", "--runtime", "docker"], check=True, env=env)
    except subprocess.CalledProcessError as exc:
        raise StackError(
            f"image build failed (exit {exc.returncode}) — see the build output above"
        ) from exc
    except OSError as exc:
        raise StackError(f"cannot run {alfredctl} to build the image: {exc}") from exc


_PORT_IN_USE_HINT = (
    " — another `alfred evals run` may be using it; runs on one branch share a container "
    "name and cannot overlap"
)


async def _start_fake(
    name: str, start: Callable[[], Awaitable[None]], host: str, port: int, option: str
) -> None:
    try:
        await start()
    except OSError as exc:
        why = " ".join(str(exc).split())
        if port:
            where = f"{host}:{port} (choose another with {option})"
        else:
            where = f"{host} (any free port)"
        if exc.errno == errno.EADDRINUSE:
            why += _PORT_IN_USE_HINT
        raise StackError(f"could not start the {name} on {where}: {why}") from exc


async def run_suites(opts: RunOptions) -> RunOutcome:
    """Preflight, build, run every suite, then write and print the scorecard for
    whatever ran. Suites whose stack failed to start are named in the outcome."""
    plan = build_plan(opts)
    vllm_url = base_url(opts.vllm_url, "--vllm-url")
    embed_url = base_url(opts.embed_url, "--embed-url")
    async with httpx.AsyncClient(timeout=10) as client:
        await check_models(client, vllm_url, opts.model)
        await check_models(client, f"{embed_url}/v1", opts.embed_model)
    if opts.build:
        hs_commit = check_home_service(opts.home_service, allow_stale=opts.allow_stale_home_service)
    else:
        # The image holds whatever home-service it was built with; the checkout may not be it.
        hs_commit = home_service_commit(opts.home_service) + NOT_REBUILT
    commit = alfred_commit(REPO_ROOT) + ("" if opts.build else NOT_REBUILT)
    report = read_calibration(CALIBRATION_FILE, opts.model)
    # A file that cannot be used is a CalibrationError naming it, a one-line preflight error.
    digests = calibration_digests(load_calibration_sets(CALIBRATION_DIR))
    calibration, trusted = calibration_for(report, opts.model, digests)
    gateway = docker_bridge_gateway()
    run_dir = opts.log_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    judge = Judge(make_judge_model(opts.model, vllm_url))

    fake_ha = FakeHA(load_world("apartment"), host=gateway, port=opts.fake_ha_port)
    proxy = LlmProxy(vllm_url.removesuffix("/v1"), host=gateway, port=opts.proxy_port)
    cfg = StackConfig(
        model=opts.model,
        vllm_url=vllm_url,
        embed_url=embed_url,
        embed_model=opts.embed_model,
        work_dir=run_dir / "data",
        home_service_dir=opts.home_service,
        keep=opts.keep,
    )

    def make_ctx(stack: Stack, variants: Sequence[ScenarioVariant]) -> RunContext:
        play_ctx = PlayContext(
            send=stack.send, fake_ha=fake_ha, proxy=proxy, reply_timeout_s=cfg.reply_timeout_s
        )
        return RunContext(
            stack=stack,
            judge=judge,
            trusted=trusted,
            variants={v.sample_id: v for v in variants},
            play_ctx=play_ctx,
        )

    use_display(opts.display)
    try:
        # Bound before the build: a concurrent run fails here, on the busy port, before it
        # retags the alfred:<branch> image another run is using.
        await _start_fake("fake HA", fake_ha.start, gateway, opts.fake_ha_port, "--fake-ha-port")
        await _start_fake("LLM proxy", proxy.start, gateway, opts.proxy_port, "--proxy-port")
        if opts.build:
            _build_image(opts.home_service)
        # After the build, which makes the image it runs. Seconds, not a boot timeout
        # minutes later, when the container cannot reach the fakes.
        await probe_host_ports({"fake HA": fake_ha.port, "LLM proxy": proxy.port}, gateway=gateway)
        # Created once the fakes are up, so a run that never starts leaves no empty dir.
        run_dir.mkdir(parents=True, exist_ok=True)
        results = await execute(
            plan,
            stack_factory=lambda: Stack(cfg, fake_ha=fake_ha, proxy=proxy),
            make_ctx=make_ctx,
            eval_fn=eval_async,
            log_dir=run_dir,
            epochs=opts.epochs,
        )
    finally:
        await proxy.stop()
        await fake_ha.stop()

    meta = RunMeta(
        run_dir=str(run_dir),
        model=opts.model,
        alfred_commit=commit,
        home_service_commit=hs_commit,
        epochs=opts.epochs,
        calibration=calibration,
        trusted=sorted(trusted),
        stacks=results.stacks,
        problems=[*results.problems, *log_problems(results.logs, opts.epochs)],
    )
    card = summarize(runs_from_logs(results.logs), meta)
    write_report(card, run_dir)
    print(render_markdown(card))
    if results.interrupted is not None:
        raise RunCancelled(run_dir)
    return RunOutcome(run_dir=run_dir, unstarted=list(results.unstarted))
