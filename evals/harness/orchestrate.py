"""`alfred evals run`: preflight, build, then one stack and one Inspect task per suite."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from inspect_ai import eval_async

from evals.harness.display import use_display
from evals.harness.driver import PlayContext
from evals.harness.fake_ha import FakeHA
from evals.harness.judge import CALIBRATION_FILE, Judge, load_report, make_judge_model
from evals.harness.net import docker_bridge_gateway
from evals.harness.preflight import (
    PreflightError,
    alfred_commit,
    check_home_service,
    check_models,
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
from evals.harness.stack import Stack, StackConfig, StackError
from evals.harness.tasks import RunContext, build_task
from evals.harness.world import load_world

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from inspect_ai import Task
    from inspect_ai.log import EvalLog

    from evals.harness.display import Display
    from evals.harness.judge import CalibrationReport
    from evals.harness.scenario import ScenarioVariant

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = REPO_ROOT / "evals" / "logs"
# With --no-build the scorecard cannot know what the image holds; it says so.
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


@dataclass(frozen=True)
class RunOutcome:
    run_dir: Path
    unstarted: list[str]  # suites whose stack failed to start; the run exits 1


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
) -> tuple[list[EvalLog], list[dict[str, Any]], dict[str, str]]:
    """Run each suite on its own stack: the logs, each stack's numbers, and a problem
    line for each suite whose stack failed to start (the next suite still runs)."""
    logs: list[EvalLog] = []
    stacks: list[dict[str, Any]] = []
    unstarted: dict[str, str] = {}
    for suite, variants in plan.items():
        stack = stack_factory()
        try:
            try:
                await stack.start()
            except StackError as exc:
                logger.error("suite %s: stack failed to start: %s", suite, exc)
                why = " ".join(str(exc).split())
                unstarted[suite] = f"suite {suite}: stack failed to start: {why}"
                continue
            ctx = make_ctx(stack, variants)
            task = build_task_fn(suite, variants, ctx, epochs)
            # No max_connections, max_retries or timeout here: inside eval_async they
            # would override the judge model's own GenerateConfig.
            logs += await eval_fn(
                task,
                model="mockllm/model",
                log_dir=str(log_dir),
                max_samples=1,
                retry_on_error=1,
                fail_on_error=False,
            )
            stacks.append(
                {
                    "suite": suite,
                    "boot_seconds": stack.boot_seconds,
                    "first_reply_ms": stack.first_reply_ms,
                    "recoveries": ctx.recoveries,
                }
            )
        finally:
            await stack.stop()
    return logs, stacks, unstarted


def calibration_for(
    report: CalibrationReport | None, model: str
) -> tuple[dict[str, float], set[str]]:
    """The judge calibration a run on *model* reports, and the categories it trusts.

    A calibration measured on another model counts as missing: its agreements say
    nothing about this judge.
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
    return {c: r.agreement for c, r in report.categories.items()}, report.trusted()


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


async def _start_fake(name: str, start: Callable[[], Awaitable[None]], host: str) -> None:
    try:
        await start()
    except OSError as exc:
        why = " ".join(str(exc).split())
        raise StackError(f"could not start the {name} on {host}: {why}") from exc


async def run_suites(opts: RunOptions) -> RunOutcome:
    """Preflight, build, run every suite, then write and print the scorecard for
    whatever ran. Suites whose stack failed to start are named in the outcome."""
    plan = build_plan(opts)
    async with httpx.AsyncClient(timeout=10) as client:
        await check_models(client, opts.vllm_url, opts.model)
        await check_models(client, f"{opts.embed_url}/v1", opts.embed_model)
    hs_commit = check_home_service(opts.home_service, allow_stale=opts.allow_stale_home_service)
    commit = alfred_commit(REPO_ROOT) + ("" if opts.build else NOT_REBUILT)
    report = read_calibration(CALIBRATION_FILE, opts.model)
    calibration, trusted = calibration_for(report, opts.model)
    gateway = docker_bridge_gateway()
    if opts.build:
        _build_image(opts.home_service)
    # Created once the fakes are up, so a run that never starts leaves no empty dir.
    run_dir = opts.log_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    judge = Judge(make_judge_model(opts.model, opts.vllm_url))

    fake_ha = FakeHA(load_world("apartment"), host=gateway)
    proxy = LlmProxy(opts.vllm_url.removesuffix("/v1"), host=gateway)
    cfg = StackConfig(
        model=opts.model,
        vllm_url=opts.vllm_url,
        embed_url=opts.embed_url,
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
        await _start_fake("fake HA", fake_ha.start, gateway)
        await _start_fake("LLM proxy", proxy.start, gateway)
        run_dir.mkdir(parents=True, exist_ok=True)
        logs, stacks, unstarted = await execute(
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
        stacks=stacks,
        problems=[*unstarted.values(), *log_problems(logs, opts.epochs)],
    )
    card = summarize(runs_from_logs(logs), meta)
    write_report(card, run_dir)
    print(render_markdown(card))
    return RunOutcome(run_dir=run_dir, unstarted=list(unstarted))
