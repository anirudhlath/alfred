"""The scorecard: per golden, per PRD row, and LLM usage by role."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast, get_args

from pydantic import BaseModel, Field

from evals.harness.checks.judge_spec import JudgeCategory
from evals.harness.checks.result import CheckResult
from evals.harness.evidence import Evidence, Role

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from inspect_ai.log import EvalLog

SCORER_NAME = "scenario_scorer"
Value = Literal["C", "I", "N", "E"]
Status = Literal["shipped", "pending"]
CELL_CHARS = 160


class LlmUsage(BaseModel):
    role: Role
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class SampleRun(BaseModel):
    sample_id: str
    scenario_id: str
    variant: int
    epoch: int
    suite: str
    status: Status
    prd: list[str]
    value: Value
    checks: list[CheckResult] = Field(default_factory=list)
    error: str | None = None
    reply_ms: list[float] = Field(default_factory=list)
    llm: list[LlmUsage] = Field(default_factory=list)


class GoldenSummary(BaseModel):
    scenario_id: str
    suite: str
    status: Status
    prd: list[str]
    variants: int
    runs: int
    passes: int
    errors: int
    inconclusive: int
    pass_rate: float | None
    pass_k: bool
    flaky: bool
    failing: list[str]


class PrdRowSummary(BaseModel):
    prd_id: str
    goldens: list[str]
    pass_rate: float | None
    pass_k: int


class RoleUsage(BaseModel):
    role: Role
    calls: int
    prompt_tokens: int
    completion_tokens: int
    p50_ms: float
    p95_ms: float


class RunMeta(BaseModel):
    run_dir: str
    model: str
    alfred_commit: str
    home_service_commit: str
    epochs: int
    calibration: dict[str, float]
    trusted: list[str]
    stacks: list[dict[str, Any]]
    # What went wrong with the run as a whole (see ``log_problems``), shown above the tables.
    problems: list[str] = Field(default_factory=list)
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Scorecard(BaseModel):
    meta: RunMeta
    goldens: list[GoldenSummary]
    prd_rows: list[PrdRowSummary]
    llm: list[RoleUsage]
    reply_p50_ms: float | None
    reply_p95_ms: float | None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank: the smallest value with at least ``q`` of the list at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def _flat(text: str) -> str:
    return " ".join(text.split())


def log_problems(logs: Sequence[EvalLog], epochs: int) -> list[str]:
    """One line per log that did not succeed, and per sample epoch it never recorded."""
    problems: list[str] = []
    for log in logs:
        task = log.eval.task
        if log.status != "success":
            message = _flat(log.error.message) if log.error is not None else "no error recorded"
            problems.append(f"{task}: {log.status} — {message}")
        sample_ids = log.eval.dataset.sample_ids
        if sample_ids is None:
            continue
        recorded = {(str(s.id), s.epoch) for s in log.samples or []}
        problems += [
            f"{task}: sample {sid} epoch {epoch} is missing from the log"
            for sid in sample_ids
            for epoch in range(1, epochs + 1)
            if (str(sid), epoch) not in recorded
        ]
    return problems


def runs_from_logs(logs: Sequence[EvalLog]) -> list[SampleRun]:
    runs: list[SampleRun] = []
    for log in logs:
        for sample in log.samples or []:
            md = sample.metadata or {}
            score = (sample.scores or {}).get(SCORER_NAME)
            raw = (sample.store or {}).get("evidence")
            evidence = Evidence.model_validate(raw) if raw else None
            value: Value = "E"
            error: str | None = None
            checks: list[CheckResult] = []
            if sample.error is not None or score is None:
                error = sample.error.message if sample.error is not None else "not scored"
            elif (score_value := str(score.value)) not in get_args(Value):
                error = f"unexpected score value {score_value}"
            else:
                value = cast("Value", score_value)
                checks = [
                    CheckResult.model_validate(c) for c in (score.metadata or {}).get("checks", [])
                ]
            status = md.get("status", "shipped")
            if status not in get_args(Status):
                # Counted as shipped so the run still shows up; the error says why it failed.
                unknown = f"unknown status {status}"
                value, status = "E", "shipped"
                error = f"{error}; {unknown}" if error else unknown
            runs.append(
                SampleRun(
                    sample_id=str(sample.id),
                    scenario_id=str(md.get("scenario_id", sample.id)),
                    variant=int(md.get("variant", 0)),
                    epoch=sample.epoch,
                    suite=str(md.get("suite", "")),
                    status=status,
                    prd=list(md.get("prd", [])),
                    value=value,
                    checks=checks,
                    error=error,
                    reply_ms=[r.latency_ms for r in evidence.replies] if evidence else [],
                    llm=[
                        LlmUsage(
                            role=c.role,
                            latency_ms=c.latency_ms,
                            prompt_tokens=c.prompt_tokens,
                            completion_tokens=c.completion_tokens,
                        )
                        for c in evidence.llm_calls
                    ]
                    if evidence
                    else [],
                )
            )
    return runs


def _golden(runs: list[SampleRun]) -> GoldenSummary:
    first = runs[0]
    passes = sum(r.value == "C" for r in runs)
    errors = sum(r.value == "E" for r in runs)
    inconclusive = sum(r.value == "N" for r in runs)
    scored = len(runs) - errors - inconclusive
    return GoldenSummary(
        scenario_id=first.scenario_id,
        suite=first.suite,
        status=first.status,
        prd=first.prd,
        variants=len({r.variant for r in runs}),
        runs=len(runs),
        passes=passes,
        errors=errors,
        inconclusive=inconclusive,
        pass_rate=passes / scored if scored else None,
        pass_k=bool(runs) and passes == len(runs),
        flaky=0 < passes < scored,
        failing=_failing(runs),
    )


def _failing(runs: list[SampleRun], limit: int = 3) -> list[str]:
    """Why a golden fell short: check failures, then harness errors, then judge errors.

    A harness error always keeps a place: when failures fill every slot, the first error
    takes the last one.
    """

    def counted(value: Value, status: str) -> list[str]:
        return [
            f"{c.name}: {c.reason}"
            for r in runs
            if r.value == value
            for c in r.checks
            if c.counted and c.status == status
        ]

    errors = [f"error: {r.error}" for r in runs if r.value == "E" and r.error]
    failing = list(dict.fromkeys([*counted("I", "fail"), *errors, *counted("N", "error")]))
    failing = failing[:limit]
    if errors and not set(errors) & set(failing):
        failing[-1] = errors[0]
    return failing


def summarize(runs: Sequence[SampleRun], meta: RunMeta) -> Scorecard:
    by_golden: dict[str, list[SampleRun]] = defaultdict(list)
    for r in runs:
        by_golden[r.scenario_id].append(r)
    goldens = sorted(
        (_golden(rs) for rs in by_golden.values()), key=lambda g: (g.suite, g.scenario_id)
    )
    by_row: dict[str, list[GoldenSummary]] = defaultdict(list)
    for g in goldens:
        if g.status == "shipped":
            for prd_id in g.prd:
                by_row[prd_id].append(g)
    rows = []
    for prd_id, gs in sorted(by_row.items()):
        rates = [g.pass_rate for g in gs if g.pass_rate is not None]
        rows.append(
            PrdRowSummary(
                prd_id=prd_id,
                goldens=[g.scenario_id for g in gs],
                pass_rate=sum(rates) / len(rates) if rates else None,
                pass_k=sum(g.pass_k for g in gs),
            )
        )
    usage: dict[Role, list[LlmUsage]] = defaultdict(list)
    for r in runs:
        for u in r.llm:
            usage[u.role].append(u)
    roles = [
        RoleUsage(
            role=role,
            calls=len(us),
            prompt_tokens=sum(u.prompt_tokens or 0 for u in us),
            completion_tokens=sum(u.completion_tokens or 0 for u in us),
            p50_ms=percentile([u.latency_ms for u in us], 0.5) or 0.0,
            p95_ms=percentile([u.latency_ms for u in us], 0.95) or 0.0,
        )
        for role, us in sorted(usage.items())
    ]
    replies = [ms for r in runs for ms in r.reply_ms]
    return Scorecard(
        meta=meta,
        goldens=goldens,
        prd_rows=rows,
        llm=roles,
        reply_p50_ms=percentile(replies, 0.5),
        reply_p95_ms=percentile(replies, 0.95),
    )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _cell(text: str) -> str:
    """Free text made safe for one table cell: one line, pipes escaped, bounded."""
    return _flat(text).replace("|", "\\|")[:CELL_CHARS]


def _judge_trust(m: RunMeta) -> str:
    if not m.calibration:
        return f"no judge calibration for {m.model} — every judge check is untrusted"
    untrusted = sorted(set(m.calibration) - set(m.trusted))
    uncalibrated = sorted(c for c in get_args(JudgeCategory) if c not in m.calibration)
    return (
        f"judge trusted: {', '.join(m.trusted) or 'none'}"
        + (f" · untrusted: {', '.join(untrusted)}" if untrusted else "")
        + (f" · uncalibrated: {', '.join(uncalibrated)}" if uncalibrated else "")
    )


def render_markdown(card: Scorecard) -> str:
    m = card.meta
    lines = [
        f"# Alfred eval scorecard — {m.finished_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"model `{m.model}` · Alfred `{m.alfred_commit[:7]}` · "
        f"home-service `{m.home_service_commit[:7]}` · {m.epochs} epochs · {_judge_trust(m)}",
        "",
    ]
    for s in m.stacks:
        lines.append(
            f"- stack `{s.get('suite')}`: boot {s.get('boot_seconds', 0) or 0:.0f} s, "
            f"first reply {(s.get('first_reply_ms') or 0) / 1000:.1f} s, "
            f"restarts {s.get('restarts', 0)}"
        )
    if m.problems:
        lines += ["", "## Run problems", ""] + [f"- {_flat(p)}" for p in m.problems]
    lines += [
        "",
        "## PRD rows",
        "",
        "| PRD row | goldens | pass rate | pass^k |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r.prd_id} | {len(r.goldens)} | {_pct(r.pass_rate)} | {r.pass_k}/{len(r.goldens)} |"
        for r in card.prd_rows
    ]
    shipped = [g for g in card.goldens if g.status == "shipped"]
    for suite in sorted({g.suite for g in shipped}):
        lines += [
            "",
            f"## {suite}",
            "",
            "| golden | variants | runs | pass rate | pass^k | flaky | errors "
            "| first failing check |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for g in (g for g in shipped if g.suite == suite):
            first = _cell(g.failing[0]) if g.failing else ""
            lines.append(
                f"| {g.scenario_id} | {g.variants} | {g.runs} | {_pct(g.pass_rate)} | "
                f"{'✓' if g.pass_k else '✗'} | {'⚠' if g.flaky else ''} | {g.errors} | {first} |"
            )
    pending = [g for g in card.goldens if g.status == "pending"]
    if pending:
        lines += [
            "",
            "## Not yet working (pending)",
            "",
            "| golden | PRD rows | pass rate |",
            "|---|---|---|",
        ]
        lines += [
            f"| {g.scenario_id} | {', '.join(g.prd)} | {_pct(g.pass_rate)} |" for g in pending
        ]
    lines += [
        "",
        "## LLM usage",
        "",
        "| role | calls | prompt tok | completion tok | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {u.role} | {u.calls} | {u.prompt_tokens} | {u.completion_tokens} | "
        f"{u.p50_ms:.0f} | {u.p95_ms:.0f} |"
        for u in card.llm
    ]
    if card.reply_p50_ms is not None:
        lines += [
            "",
            f"Reply latency: p50 {card.reply_p50_ms / 1000:.1f} s · "
            f"p95 {(card.reply_p95_ms or 0) / 1000:.1f} s",
        ]
    return "\n".join(lines) + "\n"


def write_report(card: Scorecard, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path, json_path = out_dir / "report.md", out_dir / "report.json"
    md_path.write_text(render_markdown(card))
    json_path.write_text(card.model_dump_json(indent=2))
    return md_path, json_path
