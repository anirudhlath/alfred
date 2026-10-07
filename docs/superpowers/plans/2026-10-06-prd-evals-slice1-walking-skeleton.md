# PRD Eval Suite — Slice 1 (Walking Skeleton) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `alfred evals run home_control conversation` boots a throwaway Alfred with every LLM role on vLLM, plays hand-written goldens through Alfred's real bus against a fake Home Assistant, scores them with deterministic checks plus a calibrated gemma judge, and prints a scorecard by PRD row.

**Architecture:** The harness runs on the host. A fake Home Assistant server (websockets) and a recording LLM proxy (aiohttp → vLLM) listen on the docker bridge gateway. `alfredctl up --eval` starts the real fat image with its LLMs pointed at the proxy and home-service pointed at the fake HA. Inspect AI runs one task per suite. Its solver writes `UserRequest`s to the container's Redis and collects an `Evidence` record. Its scorer runs typed checks over that evidence and asks the judge the yes/no rubric questions.

**Tech Stack:** Python 3.13, Inspect AI 0.3.277 (`eval_async`, custom solver and scorer, `mockllm/model` as the nominal task model), websockets 16, aiohttp 3.14, httpx, redis.asyncio, Typer, Pydantic v2, pytest and pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-10-06-prd-eval-suite-design.md`

## Global Constraints

- **CLI name.** The CLI is `alfred evals <command>` (the owner's instruction, 2026-10-06). Never `alfred-evals`. `python -m evals memory …` keeps working unchanged.
- **Python and checks.** Python 3.13+, Pydantic v2, async-first.
  - `mypy --strict` covers `alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/` plus the new `alfred_cli/`.
  - ruff, line length 100. Rules `B` and `TCH` are on: Typer options use `Annotated[...]` (as `alfredctl/main.py` does, never `= typer.Option(...)` defaults), and annotation-only imports move under `TYPE_CHECKING` unless Pydantic needs them at runtime. Run `.venv/bin/ruff check --fix` and `.venv/bin/ruff format` on the touched files before every commit.
  - Run tests with `.venv/bin/python -m pytest`, which is how the repo's CLAUDE.md says to run them in a worktree.
- **Inspect display.** `eval_async` has no `display` argument in 0.3.277. The display comes from the `INSPECT_DISPLAY` env var when Inspect first needs it. `tests/evals/harness/__init__.py` sets it to `none`, and `run_suites` sets it from `--display`.
- **Dependencies.** Inspect AI is pinned `inspect-ai>=0.3.277,<0.4`. It goes in the existing `evals` optional extra in `pyproject.toml`, with `aiohttp>=3.14` and `websockets>=16.0`.
- **Model in every role:** vLLM `gemma-4-26b-a4b` at `http://localhost:8000/v1`. Embeddings: `BAAI/bge-m3` at `http://localhost:8001`. Judge temperature 0.
- **Load on the shared vLLM.** The proxy allows at most **4** concurrent upstream requests. The judge model sets `max_connections=2`. Only one eval container runs at a time.
- **No real home.** Eval containers never see the operator's `.env`, the real HA host or the real token. `alfredctl up --eval` ignores `.env`, and the harness always sets `HA_HOST` to its fake HA.
- **Fixed fake credentials:**
  - HA token: `alfred-eval-ha-token`
  - OpenRouter placeholder: `alfred-eval-not-a-key`
  - secrets passphrase: `alfred-eval-not-a-secret`
  - Signal number for sir: `+15550100`; for a guest: `+15550199` (555-01xx numbers are reserved for fiction)
- **Runtime.** The harness drives docker only. `alfredctl up --eval` supports docker and podman.
- **Never `FLUSHALL`.** The reset is always a fresh container.
- **The repo is public.** Every committed file under `evals/` uses the fictional apartment: no real names, addresses, IPs or tokens.
- **Commits.** Conventional commit messages. Never put a model identifier in a commit or PR.
- **Judge trust.** A judge category below **85%** calibration agreement is untrusted: reported, not counted. A calibration made with another model counts as missing.
- **Defaults:** 3 epochs, 120 s reply timeout, 420 s boot timeout.
- **Score values:**
  - `C`: every counted check passed
  - `I`: a counted check failed
  - `N`: inconclusive (a judge error, or no counted check)
  - `E`: the harness failed

## Review Focus

1. **vLLM down, or the model renamed.** `alfred evals run` must fail before building or booting, and the error names the URL and the models it found. Test: Task 8 `test_check_models_names_what_it_found`.
2. **The container dies mid-suite** (the supervisor exits on a service's clean exit or at the restart cap). The next sample must not wait out the 120 s timeout:
   - the stack restarts once;
   - after that, every sample errors at once with the `docker logs` tail.

   Test: Task 12 `test_dead_container_restarts_once_then_errors_fast`.
3. **A malformed golden.** An unknown step key, an unknown check, two steps with variants, an id without its suite prefix, or a bare YAML `on` must fail to load, naming the file, before anything boots. Tests: Task 4.
4. **Ctrl-C or an Inspect crash during a run.** The container is removed, the root-owned data dir is wiped, and the fake HA and proxy are closed. Test: Task 12 `test_execute_tears_down_when_eval_raises`.
5. **Judge output with no `VERDICT:` line.** The check's status is `error`, and the sample scores `N`, never a pass or a fail. Test: Task 10 `test_unparseable_verdict_is_an_error_not_a_fail`.

---

## File Structure

```
alfred_cli/__init__.py            # new top-level CLI package
alfred_cli/main.py                # `alfred` Typer app; mounts `evals`
evals/__main__.py                 # rewritten: `python -m evals memory` only
evals/cli.py                      # `alfred evals list|run|calibrate|memory`
evals/coverage.yaml               # every PRD row → suites / tests / not_llm
evals/suites/conversation/*.yaml  # goldens
evals/suites/home_control/*.yaml  # goldens
evals/judge_calibration/*.yaml    # hand-labelled judge items
evals/harness/__init__.py
evals/harness/evidence.py         # Evidence and its parts
evals/harness/checks/__init__.py  # registry: DETERMINISTIC, CHECK_PARAMS, run_check
evals/harness/checks/result.py    # CheckResult
evals/harness/checks/matching.py  # value_matches, describe
evals/harness/checks/home.py      # ha_called, ha_not_called, ha_state
evals/harness/checks/llm.py       # tool_called, tool_not_called, llm_tool_args(_absent)
evals/harness/checks/reply.py     # reply_contains, reply_not_contains
evals/harness/checks/latency.py   # latency
evals/harness/checks/judge_spec.py# JudgeSpec, JudgeCategory
evals/harness/scenario.py         # Scenario schema, loader, variants, selection
evals/harness/world.py            # World model + load_world
evals/harness/worlds/apartment.yaml
evals/harness/fake_ha.py          # FakeHA server + apply_service
evals/harness/proxy.py            # LlmProxy + classify_role
evals/harness/net.py              # docker bridge gateway, in-container URLs
evals/harness/preflight.py        # vLLM + home-service checks
evals/harness/stack.py            # Stack: boot, readiness, teardown
evals/harness/driver.py           # play(): scenario → Evidence
evals/harness/judge.py            # Judge, calibration
evals/harness/report.py           # scorecard
evals/harness/tasks.py            # Inspect task, solvers, scorer
evals/harness/orchestrate.py      # run_suites / execute
evals/harness/coverage.py         # PRD parser + coverage problems
alfredctl/{runtime,launch,main,staging}.py   # --eval, eval name, home-service override
runner/__main__.py                # eval Redis bind
tests/evals/harness/__init__.py + test_*.py
tests/evals/test_cli.py, test_main_memory_only.py, test_prd_coverage.py
docs/evals.md (new), docs/evals-runner.md (deleted), docs/architecture.md, docs/containerization.md, docs/PRD.md, CLAUDE.md
```

---

### Task 1: Remove the old harness, keep the memory eval, swap dependencies

**Files:**
- Delete: `evals/models.py`, `evals/pipeline.py`, `evals/inference.py`, `evals/scorer.py`, `evals/loader.py`, `evals/store.py`, `evals/compare.py`, `evals/report.py`, `evals/context_fixtures.py`, `evals/scenarios/`, `evals/contexts/`, `evals/conscious/`, `evals/regression/`, `evals/e2e/`
- Delete: `tests/evals/test_capture_context.py`, `test_compare.py`, `test_conscious_metrics.py`, `test_conscious_runner.py`, `test_loader.py`, `test_models.py`, `test_pipeline.py`, `test_regression.py`, `test_scorer.py`, `test_store.py`
- Rewrite: `evals/__main__.py`
- Modify: `pyproject.toml` (the `evals` extra, `addopts`), `.gitignore`
- Test: `tests/evals/test_main_memory_only.py`

**Interfaces:**
- Produces: `evals.__main__.build_parser() -> argparse.ArgumentParser` and `evals.__main__.main(argv: Sequence[str] | None = None) -> None`. Task 2 calls `main(["memory", *args])`.

- [ ] **Step 1: Set up the worktree environment**

```bash
cd ~/code/.worktrees/alfred/prd-evals
uv sync --all-extras
.venv/bin/python -m pytest -q tests/evals -x
```
Expected: the suite passes on the untouched tree (baseline).

- [ ] **Step 2: Write the failing test**

```python
# tests/evals/test_main_memory_only.py
from __future__ import annotations

import argparse

from evals.__main__ import build_parser


def _subcommands(parser: argparse.ArgumentParser) -> list[str]:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sorted(action.choices)


def test_only_the_memory_subcommand_remains() -> None:
    assert _subcommands(build_parser()) == ["memory"]


def test_memory_subcommand_still_parses() -> None:
    args = build_parser().parse_args(["memory", "policies"])
    assert args.command == "memory"
```

- [ ] **Step 3: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/evals/test_main_memory_only.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_parser'`.

- [ ] **Step 4: Delete the old harness and rewrite `evals/__main__.py`**

```bash
git rm -r -q evals/models.py evals/pipeline.py evals/inference.py evals/scorer.py \
  evals/loader.py evals/store.py evals/compare.py evals/report.py evals/context_fixtures.py \
  evals/scenarios evals/contexts evals/conscious evals/regression evals/e2e \
  tests/evals/test_capture_context.py tests/evals/test_compare.py \
  tests/evals/test_conscious_metrics.py tests/evals/test_conscious_runner.py \
  tests/evals/test_loader.py tests/evals/test_models.py tests/evals/test_pipeline.py \
  tests/evals/test_regression.py tests/evals/test_scorer.py tests/evals/test_store.py
```

Before replacing `evals/__main__.py`, read its current `main()`. If anything runs before `run_memory_command(args)`, such as a logging setup call, carry that line over unchanged into the new `main()`.

```python
# evals/__main__.py
"""``python -m evals memory …`` — the memory-decay simulation (docs/evals-memory.md).

The PRD suites run with ``alfred evals`` (docs/evals.md).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from evals.memory.cli import add_memory_parser, run_memory_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Alfred's memory-decay eval. The PRD suites run with `alfred evals`.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add_memory_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_memory_command(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Check that nothing else imports the deleted modules**

Run: `grep -rnE "evals\.(models|pipeline|inference|scorer|loader|store|compare|report|context_fixtures|conscious|regression|e2e)\b" --include='*.py' . | grep -v '^./.venv'`
Expected: no output. If anything matches, it was an old-harness caller. Delete it if it belonged to the old harness, otherwise stop and report it.

- [ ] **Step 6: Swap the `evals` extra and drop the deepeval guard**

In `pyproject.toml`, replace the `evals` extra:

```toml
evals = [
    "inspect-ai>=0.3.277,<0.4",
    "aiohttp>=3.14",
    "websockets>=16.0",
]
```

Delete the whole comment block above `addopts`, and the `addopts = "-p no:deepeval"` line, from `[tool.pytest.ini_options]`. deepeval is gone, so the guard has nothing left to guard.

In `.gitignore`, next to `evals/runs/`, add:

```
evals/logs/
```

- [ ] **Step 7: Lock, sync, test, type-check**

```bash
uv lock && uv sync --all-extras
.venv/bin/python -m pytest -q tests/evals
.venv/bin/mypy --strict evals/
.venv/bin/python -m evals memory --help
```
Expected: the tests pass (memory tests and the new test), mypy is clean, and the memory help prints.

- [ ] **Step 8: Commit**

```bash
git add -A evals tests/evals pyproject.toml uv.lock .gitignore
git commit -m "refactor(evals): remove the old eval harness, keep the memory-decay eval"
```

---

### Task 2: The `alfred` CLI with an `evals` group

**Files:**
- Create: `alfred_cli/__init__.py`, `alfred_cli/main.py`, `evals/cli.py`
- Modify: `pyproject.toml` (`[project.scripts]`, `[tool.setuptools.packages.find]`), `.github/workflows/ci.yml` (`mypy-targets`)
- Test: `tests/evals/test_cli.py`

**Interfaces:**
- Consumes: `evals.__main__.main(argv)` (Task 1).
- Produces:
  - `evals.cli.evals_app: typer.Typer`. Tasks 10 and 12 add the `calibrate`, `list` and `run` commands.
  - `alfred_cli.main.app`, the `alfred` console script.

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/test_cli.py
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from alfred_cli.main import app

runner = CliRunner()


def test_alfred_has_an_evals_group() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "evals" in result.output


def test_evals_memory_passes_its_arguments_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr("evals.__main__.main", lambda argv: seen.append(list(argv)))
    result = runner.invoke(app, ["evals", "memory", "runs", "--limit", "3"])
    assert result.exit_code == 0, result.output
    assert seen == [["memory", "runs", "--limit", "3"]]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/evals/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alfred_cli'`.

- [ ] **Step 3: Implement**

```python
# alfred_cli/__init__.py
"""``alfred`` — the operator command line."""
```

```python
# alfred_cli/main.py
"""``alfred`` — the operator command line. Groups: ``evals``."""

from __future__ import annotations

import typer

from evals.cli import evals_app

app = typer.Typer(no_args_is_help=True, help="Alfred command line.")
app.add_typer(evals_app, name="evals")
```

```python
# evals/cli.py
"""``alfred evals`` — the PRD eval suites (docs/evals.md) and the memory-decay simulation.

Imports of the harness stay inside the commands: ``inspect_ai`` ships in the ``evals``
extra, and ``alfred --help`` must work without it.
"""

from __future__ import annotations

import typer

evals_app = typer.Typer(no_args_is_help=True, help="Evaluate Alfred against its PRD.")


@evals_app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def memory(ctx: typer.Context) -> None:
    """Memory-decay simulation — same arguments as `python -m evals memory`."""
    from evals.__main__ import main as memory_main

    memory_main(["memory", *ctx.args])
```

In `pyproject.toml`:

```toml
[project.scripts]
alfredctl = "alfredctl.main:app"
alfred = "alfred_cli.main:app"
```

and add `"alfred_cli*"` to the `include` list under `[tool.setuptools.packages.find]`.

In `.github/workflows/ci.yml`, add `alfred_cli/` to the `mypy-targets` string, so it reads `"alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/"`.

- [ ] **Step 4: Sync and run the tests**

```bash
uv sync --all-extras
.venv/bin/python -m pytest tests/evals/test_cli.py -v
.venv/bin/alfred evals --help
.venv/bin/mypy --strict alfred_cli/ evals/
```
Expected: PASS. The help lists `memory`, and mypy is clean.

- [ ] **Step 5: Commit**

```bash
git add alfred_cli evals/cli.py tests/evals/test_cli.py pyproject.toml uv.lock .github/workflows/ci.yml
git commit -m "feat(cli): add the alfred command with an evals group"
```

---

### Task 3: Evidence and the deterministic checks

**Files:**
- Create: `evals/harness/__init__.py`, `evals/harness/evidence.py`, and `evals/harness/checks/` with `__init__.py`, `result.py`, `matching.py`, `home.py`, `llm.py`, `reply.py`, `latency.py`, `judge_spec.py`
- Test: `tests/evals/harness/__init__.py`, `tests/evals/harness/factories.py`, `tests/evals/harness/test_checks.py`

**Interfaces:**
- Produces:
  - `Evidence`, `Reply`, `HaCall`, `HaState`, `ToolCall`, `LlmCall`, `TranscriptTurn` and `Role` in `evals.harness.evidence`.
  - `CheckResult(name, status: "pass"|"fail"|"error", reason, counted=True)`.
  - `DETERMINISTIC: dict[str, tuple[type[BaseModel], fn]]` and `CHECK_PARAMS: dict[str, type[BaseModel]]`, which includes `"judge": JudgeSpec`.
  - `run_check(name, params, evidence) -> CheckResult`.
  - `JudgeSpec(category, rubric, reference=None)` and `JudgeCategory`.
- **Index conventions,** which every later task relies on:
  - `Reply.step` is the index into the variant's `steps`.
  - Check params named `step` (reply and latency checks) index **`evidence.replies`**, where `-1` means the last reply.
  - `after_step` (`ha_called`) indexes **`evidence.step_started`**.
- **Tool names:** System 2 sends `home_light_turn_on` for `home.light_turn_on`, so both sides of a comparison go through `normalize_tool` (`.` → `_`).

- [ ] **Step 1: Write the test factory and the failing tests**

```python
# tests/evals/harness/__init__.py
import os

# Inspect reads its console display from this on first use; tests never want a TUI.
os.environ.setdefault("INSPECT_DISPLAY", "none")
```

```python
# tests/evals/harness/factories.py
from __future__ import annotations

from typing import Any

from evals.harness.evidence import Evidence, HaCall, HaState, LlmCall, Reply, ToolCall


def evidence(
    *,
    replies: list[str] | None = None,
    ha_calls: list[HaCall] | None = None,
    ha_states: dict[str, HaState] | None = None,
    llm_calls: list[LlmCall] | None = None,
    step_started: list[float] | None = None,
    latencies: list[float] | None = None,
) -> Evidence:
    texts = replies or []
    lat = latencies or [1000.0] * len(texts)
    return Evidence(
        scenario_id="suite.case",
        variant=0,
        epoch=1,
        session_id="eval-test",
        started_at=0.0,
        ended_at=100.0,
        step_started=step_started or [0.0],
        replies=[
            Reply(step=i, text=t, source="conscious-engine", latency_ms=lat[i])
            for i, t in enumerate(texts)
        ],
        ha_calls=ha_calls or [],
        ha_states=ha_states or {},
        llm_calls=llm_calls or [],
    )


def call(domain: str, service: str, ids: list[str], data: dict[str, Any] | None = None,
         t: float = 1.0) -> HaCall:
    return HaCall(t=t, domain=domain, service=service, service_data=data or {}, entity_ids=ids)


def llm(role: str, *calls: tuple[str, dict[str, Any]]) -> LlmCall:
    return LlmCall(
        t=1.0, role=role, latency_ms=10.0, status=200,  # type: ignore[arg-type]
        tool_calls=[ToolCall(name=n, arguments=a) for n, a in calls],
    )
```

```python
# tests/evals/harness/test_checks.py
from __future__ import annotations

from evals.harness.checks import CHECK_PARAMS, run_check
from evals.harness.evidence import HaState
from tests.evals.harness.factories import call, evidence, llm


def check(name: str, params: dict, ev):  # type: ignore[no-untyped-def]
    return run_check(name, CHECK_PARAMS[name].model_validate(params), ev)


def test_ha_called_matches_domain_service_entities_and_approx_data() -> None:
    ev = evidence(ha_calls=[call("light", "turn_on", ["light.a", "light.b"], {"brightness_pct": 31.0})])
    ok = check("ha_called", {"domain": "light", "service": "turn_on", "entity_id": "light.a",
                             "data": {"brightness_pct": {"approx": 30, "tol": 2}}}, ev)
    assert ok.status == "pass"
    far = check("ha_called", {"domain": "light", "service": "turn_on",
                              "data": {"brightness_pct": {"approx": 50, "tol": 2}}}, ev)
    assert far.status == "fail" and "brightness_pct" in far.reason


def test_ha_called_after_step_ignores_earlier_calls() -> None:
    ev = evidence(ha_calls=[call("light", "turn_on", ["light.a"], t=1.0)], step_started=[0.0, 5.0])
    res = check("ha_called", {"domain": "light", "service": "turn_on", "after_step": 1}, ev)
    assert res.status == "fail"


def test_ha_not_called_with_no_filters_means_no_calls_at_all() -> None:
    assert check("ha_not_called", {}, evidence()).status == "pass"
    ev = evidence(ha_calls=[call("switch", "turn_on", ["switch.x"])])
    res = check("ha_not_called", {}, ev)
    assert res.status == "fail" and "switch.turn_on" in res.reason


def test_ha_not_called_by_entity_only() -> None:
    ev = evidence(ha_calls=[call("light", "turn_off", ["light.a"])])
    assert check("ha_not_called", {"entity_id": "light.b"}, ev).status == "pass"
    assert check("ha_not_called", {"entity_id": "light.a"}, ev).status == "fail"


def test_ha_state_checks_state_and_attributes() -> None:
    ev = evidence(ha_states={"light.a": HaState(state="on", attributes={"brightness": 130})})
    assert check("ha_state", {"entity_id": "light.a", "state": "on",
                              "attributes": {"brightness": {"approx": 128, "tol": 13}}}, ev).status == "pass"
    assert check("ha_state", {"entity_id": "light.a", "state": "off"}, ev).status == "fail"
    assert check("ha_state", {"entity_id": "light.zzz"}, ev).status == "fail"


def test_tool_names_compare_after_dot_normalisation() -> None:
    ev = evidence(llm_calls=[llm("system2", ("home_light_turn_on", {"brightness_pct": 30}))])
    assert check("tool_called", {"tool": "home.light_turn_on"}, ev).status == "pass"
    assert check("tool_called", {"tool": "home.light_turn_on", "role": "system1"}, ev).status == "fail"
    assert check("tool_not_called", {"tool": "home.call_service"}, ev).status == "pass"


def test_llm_tool_args_and_absent() -> None:
    ev = evidence(llm_calls=[llm("system2", ("triggers_create_trigger", {"run_in_seconds": 1200}))])
    assert check("llm_tool_args", {"tool": "triggers.create_trigger",
                                   "args": {"run_in_seconds": {"approx": 1200, "tol": 60}}}, ev).status == "pass"
    assert check("llm_tool_args_absent", {"tool": "triggers.create_trigger", "key": "run_at"}, ev).status == "pass"
    assert check("llm_tool_args_absent", {"tool": "triggers.create_trigger",
                                          "key": "run_in_seconds"}, ev).status == "fail"


def test_reply_contains_defaults_to_last_reply_and_is_case_insensitive() -> None:
    ev = evidence(replies=["Priya is visiting.", "The door is LOCKED, sir."])
    assert check("reply_contains", {"text": "locked"}, ev).status == "pass"
    assert check("reply_contains", {"text": "priya"}, ev).status == "fail"
    assert check("reply_contains", {"text": "priya", "step": "any"}, ev).status == "pass"
    assert check("reply_contains", {"any": ["nope", "door"]}, ev).status == "pass"


def test_reply_regex_word_boundary_keeps_unlocked_out() -> None:
    ev = evidence(replies=["The front door is unlocked, sir."])
    assert check("reply_contains", {"regex": r"\blocked\b"}, ev).status == "fail"
    assert check("reply_not_contains", {"text": "unlocked"}, ev).status == "fail"


def test_reply_check_without_reply_fails_with_reason() -> None:
    res = check("reply_contains", {"text": "x", "step": 3}, evidence(replies=["a"]))
    assert res.status == "fail" and "no reply" in res.reason


def test_latency_bound() -> None:
    ev = evidence(replies=["ok"], latencies=[4200.0])
    assert check("latency", {"metric": "reply_ms", "max": 5000}, ev).status == "pass"
    assert check("latency", {"metric": "reply_ms", "max": 4000}, ev).status == "fail"


def test_reply_params_need_exactly_one_needle() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CHECK_PARAMS["reply_contains"].model_validate({"text": "a", "regex": "b"})


def test_judge_is_a_known_check_name() -> None:
    assert "judge" in CHECK_PARAMS
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_checks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.harness'`.

- [ ] **Step 3: Implement `evidence.py`**

```python
# evals/harness/__init__.py
"""The PRD eval harness (docs/evals.md)."""
```

```python
# evals/harness/evidence.py
"""What one scenario run produced — the only input the checks and the judge see.

Times are ``time.monotonic()`` seconds. The proxy, the fake HA and the driver share
one process, so their clocks agree.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system1", "system2", "librarian", "unknown"]


class TranscriptTurn(BaseModel):
    role: Literal["user", "alfred", "event"]
    text: str


class Reply(BaseModel):
    step: int  # index into the variant's steps
    text: str
    source: str
    actions_taken: list[str] = Field(default_factory=list)
    latency_ms: float


class HaCall(BaseModel):
    t: float
    domain: str
    service: str
    service_data: dict[str, Any] = Field(default_factory=dict)
    entity_ids: list[str] = Field(default_factory=list)


class HaState(BaseModel):
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LlmCall(BaseModel):
    t: float
    role: Role
    latency_ms: float
    status: int
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools_offered: list[str] = Field(default_factory=list)
    response_text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class Evidence(BaseModel):
    scenario_id: str
    variant: int
    epoch: int
    session_id: str
    started_at: float
    ended_at: float
    step_started: list[float] = Field(default_factory=list)
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    replies: list[Reply] = Field(default_factory=list)
    ha_calls: list[HaCall] = Field(default_factory=list)
    ha_states: dict[str, HaState] = Field(default_factory=dict)
    llm_calls: list[LlmCall] = Field(default_factory=list)

    def calls_after_step(self, step: int | None) -> list[HaCall]:
        if step is None:
            return list(self.ha_calls)
        start = self.step_started[step]
        return [c for c in self.ha_calls if c.t >= start]
```

- [ ] **Step 4: Implement the checks**

```python
# evals/harness/checks/result.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CheckResult(BaseModel):
    name: str
    status: Literal["pass", "fail", "error"]
    reason: str
    counted: bool = True


def passed(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status="pass", reason=reason)


def failed(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status="fail", reason=reason)
```

```python
# evals/harness/checks/matching.py
"""Expected-vs-actual matching shared by the check families.

An expected value is a plain scalar, a list (each element must match some actual
element), or ``{approx: x, tol: t}`` for numbers. Strings compare case-insensitively.
"""

from __future__ import annotations

import json
from typing import Any


def as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _is_approx(expected: object) -> bool:
    return isinstance(expected, dict) and "approx" in expected and set(expected) <= {"approx", "tol"}


def value_matches(expected: object, actual: object) -> bool:
    if _is_approx(expected):
        assert isinstance(expected, dict)
        number = as_number(actual)
        target = as_number(expected["approx"])
        tol = as_number(expected.get("tol", 0)) or 0.0
        return number is not None and target is not None and abs(number - target) <= tol
    if isinstance(expected, bool):
        if isinstance(actual, str):
            return actual.strip().lower() == str(expected).lower()
        return actual is expected
    if isinstance(expected, int | float):
        number = as_number(actual)
        return number is not None and number == float(expected)
    if isinstance(expected, str):
        return isinstance(actual, str) and actual.strip().lower() == expected.strip().lower()
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(value_matches(e, a) for a in actual) for e in expected
        )
    return expected == actual


def describe(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)
```

```python
# evals/harness/checks/home.py
"""Checks over what the fake Home Assistant saw and holds."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from evals.harness.checks.matching import describe, value_matches
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Evidence, HaCall

EntityIds = str | list[str]


def _ids(value: EntityIds | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


class HaCalledParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str
    service: str
    entity_id: EntityIds | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    after_step: int | None = None


class HaNotCalledParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str | None = None
    service: str | None = None
    entity_id: EntityIds | None = None


class HaStateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    state: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


def _matches(
    call: HaCall, domain: str | None, service: str | None, ids: list[str], data: dict[str, Any]
) -> bool:
    if domain is not None and call.domain != domain:
        return False
    if service is not None and call.service != service:
        return False
    if ids and not set(ids) <= set(call.entity_ids):
        return False
    return all(k in call.service_data and value_matches(v, call.service_data[k]) for k, v in data.items())


def _fmt(calls: list[HaCall]) -> str:
    return "; ".join(
        f"{c.domain}.{c.service} {c.entity_ids} {describe(c.service_data)}" for c in calls
    ) or "none"


def ha_called(evidence: Evidence, p: HaCalledParams) -> CheckResult:
    calls = evidence.calls_after_step(p.after_step)
    want = f"{p.domain}.{p.service} {_ids(p.entity_id)} {describe(p.data)}"
    if any(_matches(c, p.domain, p.service, _ids(p.entity_id), p.data) for c in calls):
        return passed("ha_called", f"saw {want}")
    return failed("ha_called", f"wanted {want}; HA calls: {_fmt(calls)}")


def ha_not_called(evidence: Evidence, p: HaNotCalledParams) -> CheckResult:
    bad = [c for c in evidence.ha_calls if _matches(c, p.domain, p.service, _ids(p.entity_id), {})]
    if bad:
        return failed("ha_not_called", f"unexpected HA calls: {_fmt(bad)}")
    return passed("ha_not_called", "no matching HA call")


def ha_state(evidence: Evidence, p: HaStateParams) -> CheckResult:
    state = evidence.ha_states.get(p.entity_id)
    if state is None:
        return failed("ha_state", f"{p.entity_id} is not in the fake HA")
    if p.state is not None and state.state != p.state:
        return failed("ha_state", f"{p.entity_id} is {state.state!r}, wanted {p.state!r}")
    for key, want in p.attributes.items():
        if not value_matches(want, state.attributes.get(key)):
            return failed(
                "ha_state",
                f"{p.entity_id}.{key} is {describe(state.attributes.get(key))}, wanted {describe(want)}",
            )
    return passed("ha_state", f"{p.entity_id} is {state.state!r}")
```

```python
# evals/harness/checks/llm.py
"""Checks over the LLM calls the proxy recorded."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from evals.harness.checks.matching import describe, value_matches
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Evidence, Role, ToolCall


def normalize_tool(name: str) -> str:
    """System 2 sends ``home_light_turn_on`` for ``home.light_turn_on`` (engine.py)."""
    return name.replace(".", "_")


class ToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    role: Role = "system2"


class ToolArgsParams(ToolParams):
    args: dict[str, Any]


class ToolArgAbsentParams(ToolParams):
    key: str


def _calls(evidence: Evidence, role: Role, tool: str) -> list[ToolCall]:
    want = normalize_tool(tool)
    return [
        tc
        for c in evidence.llm_calls
        if c.role == role
        for tc in c.tool_calls
        if normalize_tool(tc.name) == want
    ]


def _names(evidence: Evidence, role: Role) -> str:
    names = sorted({tc.name for c in evidence.llm_calls if c.role == role for tc in c.tool_calls})
    return ", ".join(names) or "nothing"


def tool_called(evidence: Evidence, p: ToolParams) -> CheckResult:
    if _calls(evidence, p.role, p.tool):
        return passed("tool_called", f"{p.role} called {p.tool}")
    return failed("tool_called", f"{p.role} never called {p.tool}; it called {_names(evidence, p.role)}")


def tool_not_called(evidence: Evidence, p: ToolParams) -> CheckResult:
    if _calls(evidence, p.role, p.tool):
        return failed("tool_not_called", f"{p.role} called {p.tool}")
    return passed("tool_not_called", f"{p.role} did not call {p.tool}")


def llm_tool_args(evidence: Evidence, p: ToolArgsParams) -> CheckResult:
    calls = _calls(evidence, p.role, p.tool)
    for tc in calls:
        if all(k in tc.arguments and value_matches(v, tc.arguments[k]) for k, v in p.args.items()):
            return passed("llm_tool_args", f"{p.tool} called with {describe(tc.arguments)}")
    seen = "; ".join(describe(tc.arguments) for tc in calls) or "no calls"
    return failed("llm_tool_args", f"wanted {p.tool} with {describe(p.args)}; saw {seen}")


def llm_tool_args_absent(evidence: Evidence, p: ToolArgAbsentParams) -> CheckResult:
    bad = [tc for tc in _calls(evidence, p.role, p.tool) if p.key in tc.arguments]
    if bad:
        return failed("llm_tool_args_absent", f"{p.tool} was given {p.key}={describe(bad[0].arguments[p.key])}")
    return passed("llm_tool_args_absent", f"{p.tool} never given {p.key}")
```

```python
# evals/harness/checks/reply.py
"""Checks over Alfred's replies. ``step`` indexes evidence.replies; -1 is the last."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Evidence, Reply


class ReplyTextParams(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str | None = None
    any_of: list[str] | None = Field(default=None, alias="any")
    regex: str | None = None
    step: int | Literal["any"] = -1

    @model_validator(mode="after")
    def _exactly_one_needle(self) -> Self:
        if sum(x is not None for x in (self.text, self.any_of, self.regex)) != 1:
            raise ValueError("give exactly one of text, any, regex")
        return self


def _replies(evidence: Evidence, step: int | Literal["any"]) -> list[Reply]:
    if step == "any":
        return list(evidence.replies)
    try:
        return [evidence.replies[step]]
    except IndexError:
        return []


def _hit(p: ReplyTextParams, text: str) -> str | None:
    lowered = text.lower()
    if p.text is not None:
        return p.text if p.text.lower() in lowered else None
    if p.any_of is not None:
        return next((n for n in p.any_of if n.lower() in lowered), None)
    assert p.regex is not None
    m = re.search(p.regex, text, re.IGNORECASE)
    return m.group(0) if m else None


def _needle(p: ReplyTextParams) -> str:
    return p.text or (" | ".join(p.any_of) if p.any_of else f"/{p.regex}/")


def reply_contains(evidence: Evidence, p: ReplyTextParams) -> CheckResult:
    replies = _replies(evidence, p.step)
    if not replies:
        return failed("reply_contains", f"no reply at step {p.step}")
    for r in replies:
        if (hit := _hit(p, r.text)) is not None:
            return passed("reply_contains", f"found {hit!r}")
    return failed("reply_contains", f"{_needle(p)} not in {replies[-1].text[:200]!r}")


def reply_not_contains(evidence: Evidence, p: ReplyTextParams) -> CheckResult:
    for r in _replies(evidence, p.step):
        if (hit := _hit(p, r.text)) is not None:
            return failed("reply_not_contains", f"reply contains {hit!r}: {r.text[:200]!r}")
    return passed("reply_not_contains", f"{_needle(p)} absent")
```

```python
# evals/harness/checks/latency.py
"""Latency bounds. Slice 1 measures reply_ms; reflex_ms and reminder_fire_ms come later."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Evidence


class LatencyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    metric: Literal["reply_ms"]
    max_ms: float = Field(gt=0, alias="max")
    step: int = -1


def latency(evidence: Evidence, p: LatencyParams) -> CheckResult:
    try:
        reply = evidence.replies[p.step]
    except IndexError:
        return failed("latency", f"no reply at step {p.step}")
    took = reply.latency_ms
    if took <= p.max_ms:
        return passed("latency", f"reply in {took:.0f} ms (≤ {p.max_ms:.0f})")
    return failed("latency", f"reply took {took:.0f} ms, bound {p.max_ms:.0f}")
```

```python
# evals/harness/checks/judge_spec.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JudgeCategory = Literal["tone", "answered", "faithfulness", "privacy", "relevance"]


class JudgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: JudgeCategory
    rubric: str = Field(min_length=10)
    reference: str | None = None
```

```python
# evals/harness/checks/__init__.py
"""Check registry: name → (params model, function). The judge is scored separately."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from evals.harness.checks import home, latency, llm, reply
from evals.harness.checks.judge_spec import JudgeSpec
from evals.harness.checks.result import CheckResult
from evals.harness.evidence import Evidence

DeterministicCheck = Callable[[Evidence, Any], CheckResult]

DETERMINISTIC: dict[str, tuple[type[BaseModel], DeterministicCheck]] = {
    "ha_called": (home.HaCalledParams, home.ha_called),
    "ha_not_called": (home.HaNotCalledParams, home.ha_not_called),
    "ha_state": (home.HaStateParams, home.ha_state),
    "tool_called": (llm.ToolParams, llm.tool_called),
    "tool_not_called": (llm.ToolParams, llm.tool_not_called),
    "llm_tool_args": (llm.ToolArgsParams, llm.llm_tool_args),
    "llm_tool_args_absent": (llm.ToolArgAbsentParams, llm.llm_tool_args_absent),
    "reply_contains": (reply.ReplyTextParams, reply.reply_contains),
    "reply_not_contains": (reply.ReplyTextParams, reply.reply_not_contains),
    "latency": (latency.LatencyParams, latency.latency),
}

CHECK_PARAMS: dict[str, type[BaseModel]] = {n: p for n, (p, _) in DETERMINISTIC.items()} | {
    "judge": JudgeSpec
}


def run_check(name: str, params: BaseModel, evidence: Evidence) -> CheckResult:
    """Run one deterministic check. A bug in a check scores ``error``, never ``fail``."""
    _, fn = DETERMINISTIC[name]
    try:
        return fn(evidence, params)
    except Exception as exc:  # noqa: BLE001 — a check bug must not read as Alfred failing
        return CheckResult(name=name, status="error", reason=f"check raised {type(exc).__name__}: {exc}")


__all__ = ["CHECK_PARAMS", "DETERMINISTIC", "CheckResult", "JudgeSpec", "run_check"]
```

- [ ] **Step 5: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_checks.py -v && .venv/bin/mypy --strict evals/`
Expected: PASS and clean. If ruff's config does not know `BLE001`, drop the `noqa` code and keep the comment.

- [ ] **Step 6: Commit**

```bash
git add evals/harness tests/evals/harness
git commit -m "feat(evals): evidence model and deterministic checks"
```

---

### Task 4: The scenario schema and loader

**Files:**
- Create: `evals/harness/scenario.py`
- Test: `tests/evals/harness/test_scenario.py`

**Interfaces:**
- Consumes: `CHECK_PARAMS` (Task 3).
- Produces:
  - Models: `Actor(who, channel, tz)`, `UserStep(user, variants, actor [alias "as"])`, `HaEvent`, `HaEventStep(ha_event, settle=3.0)`, `WaitStep(wait)`, `Step`, `CheckSpec(name, params)`, `Scenario`, `ScenarioVariant(scenario, variant, steps)` with `.sample_id`.
  - Errors: `ScenarioError`.
  - Functions:
    - `load_scenario(path, suite) -> Scenario`
    - `available_suites(root=SUITES_DIR) -> list[str]`
    - `load_suites(names=None, root=SUITES_DIR) -> dict[str, list[Scenario]]`
    - `select(scenarios, *, tags=(), include_pending=False) -> list[Scenario]`
    - `expand_variants(s) -> list[ScenarioVariant]`
  - Constants: `SUITES_DIR`.
- **Sample ids:** the original wording is `<scenario id>`, and each variant is `<scenario id>~<n>`, with n counted from 1.

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/harness/test_scenario.py
from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.scenario import (
    HaEventStep,
    ScenarioError,
    UserStep,
    expand_variants,
    load_suites,
    select,
)

GOOD = """
id: demo.lights.on
prd: [4.4.lights-scenes]
status: shipped
tags: [lights]
as: {who: sir, channel: signal}
steps:
  - user: "Turn on the bedroom lamp."
    variants: ["Bedroom lamp on.", "Lamp in the bedroom, please."]
  - ha_event: {entity_id: light.x, state: "on"}
expect:
  - ha_called: {domain: light, service: turn_on, entity_id: light.bedroom_lamp}
  - judge: {category: answered, rubric: "Does the reply confirm the lamp is on?"}
"""


def write(root: Path, suite: str, name: str, body: str) -> Path:
    path = root / suite / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_loads_a_good_scenario(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    [s] = load_suites(root=tmp_path)["demo"]
    assert s.suite == "demo" and s.actor.channel == "signal"
    assert isinstance(s.steps[0], UserStep) and isinstance(s.steps[1], HaEventStep)
    assert [c.name for c in s.expect] == ["ha_called", "judge"]


def test_variants_expand_to_separate_samples(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    [s] = load_suites(root=tmp_path)["demo"]
    variants = expand_variants(s)
    assert [v.sample_id for v in variants] == ["demo.lights.on", "demo.lights.on~1", "demo.lights.on~2"]
    first = variants[1].steps[0]
    assert isinstance(first, UserStep) and first.user == "Bedroom lamp on." and first.variants == []


@pytest.mark.parametrize(
    ("needle", "replacement", "message"),
    [
        ("  - ha_called:", "  - ha_callled:", "unknown check"),
        ("  - ha_event:", "  - ha_evnt:", "needs one of user, ha_event, wait"),
        ("id: demo.lights.on", "id: other.lights.on", "must start with 'demo.'"),
        ('state: "on"', "state: on", "valid string"),
        ("service: turn_on,", "", "service"),
    ],
)
def test_bad_scenarios_name_the_file(
    tmp_path: Path, needle: str, replacement: str, message: str
) -> None:
    path = write(tmp_path, "demo", "bad.yaml", GOOD.replace(needle, replacement))
    with pytest.raises(ScenarioError) as err:
        load_suites(root=tmp_path)
    assert str(path) in str(err.value) and message in str(err.value)


def test_two_steps_with_variants_is_an_error(tmp_path: Path) -> None:
    body = GOOD.replace('  - ha_event: {entity_id: light.x, state: "on"}',
                        '  - user: "and again"\n    variants: ["again"]')
    write(tmp_path, "demo", "bad.yaml", body)
    with pytest.raises(ScenarioError, match="only one step may have variants"):
        load_suites(root=tmp_path)


def test_duplicate_ids_across_files(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    write(tmp_path, "demo", "b.yaml", GOOD)
    with pytest.raises(ScenarioError, match="duplicate id"):
        load_suites(root=tmp_path)


def test_unknown_suite_lists_the_available_ones(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    with pytest.raises(ScenarioError, match="available: demo"):
        load_suites(["nope"], root=tmp_path)


def test_select_filters_pending_and_tags(tmp_path: Path) -> None:
    write(tmp_path, "demo", "a.yaml", GOOD)
    write(tmp_path, "demo", "b.yaml", GOOD.replace("demo.lights.on", "demo.lights.off")
          .replace("status: shipped", "status: pending").replace("[lights]", "[other]"))
    scenarios = load_suites(root=tmp_path)["demo"]
    assert [s.id for s in select(scenarios)] == ["demo.lights.on"]
    assert len(select(scenarios, include_pending=True)) == 2
    assert [s.id for s in select(scenarios, tags=["other"], include_pending=True)] == ["demo.lights.off"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_scenario.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.harness.scenario'`.

- [ ] **Step 3: Implement**

```python
# evals/harness/scenario.py
"""Goldens: one YAML file per golden under evals/suites/<suite>/ (docs/evals.md).

YAML gotcha: a bare ``on``/``off`` is a boolean. HA states must be quoted
(``state: "on"``); the str fields here reject booleans, so the mistake fails loudly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from evals.harness.checks import CHECK_PARAMS

SUITES_DIR = Path(__file__).resolve().parent.parent / "suites"

Who = Literal["sir", "guest"]
Channel = Literal["web_pwa", "signal", "voice", "ios", "satellite"]
_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


class ScenarioError(ValueError):
    """A golden failed to load. The message starts with the file path."""


class Actor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    who: Who = "sir"
    channel: Channel = "web_pwa"
    tz: str | None = "America/Denver"


class UserStep(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    user: str = Field(min_length=1)
    variants: list[str] = Field(default_factory=list)
    actor: Actor | None = Field(default=None, alias="as")


class HaEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class HaEventStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ha_event: HaEvent
    settle: float = Field(default=3.0, ge=0)


class WaitStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wait: float = Field(gt=0, le=600)


Step = UserStep | HaEventStep | WaitStep
_STEP_TYPES: dict[str, type[BaseModel]] = {"user": UserStep, "ha_event": HaEventStep, "wait": WaitStep}


class CheckSpec(BaseModel):
    """One ``expect`` entry: a single-key mapping ``{check_name: params}``."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    name: str
    params: Any

    @model_validator(mode="before")
    @classmethod
    def _from_mapping(cls, data: Any) -> Any:
        if isinstance(data, dict) and set(data) != {"name", "params"}:
            if len(data) != 1:
                raise ValueError(f"a check is a single-key mapping, got keys {sorted(data)}")
            ((name, params),) = data.items()
            if name not in CHECK_PARAMS:
                raise ValueError(f"unknown check {name!r}; known: {sorted(CHECK_PARAMS)}")
            return {"name": name, "params": CHECK_PARAMS[name].model_validate(params or {})}
        return data


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    prd: list[str] = Field(min_length=1)
    status: Literal["shipped", "pending"]
    tags: list[str] = Field(default_factory=list)
    world: str = "apartment"
    isolated: bool = False
    actor: Actor = Field(default_factory=Actor, alias="as")
    steps: list[Step] = Field(min_length=1)
    expect: list[CheckSpec] = Field(min_length=1)
    suite: str = ""
    path: str = ""

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError(f"id {value!r} must look like suite.topic.case (lowercase, dots)")
        return value

    @field_validator("steps", mode="before")
    @classmethod
    def _typed_steps(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        typed: list[BaseModel] = []
        for i, raw in enumerate(value):
            keys = set(raw) if isinstance(raw, dict) else set()
            kinds = keys & set(_STEP_TYPES)
            if len(kinds) != 1:
                raise ValueError(f"step {i}: needs one of user, ha_event, wait; got {sorted(keys)}")
            typed.append(_STEP_TYPES[kinds.pop()].model_validate(raw))
        return typed

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        with_variants = [s for s in self.steps if isinstance(s, UserStep) and s.variants]
        if len(with_variants) > 1:
            raise ValueError("only one step may have variants")
        needs_reply = {"judge", "reply_contains", "reply_not_contains", "latency"}
        if any(c.name in needs_reply for c in self.expect) and not any(
            isinstance(s, UserStep) for s in self.steps
        ):
            raise ValueError("reply and judge checks need at least one user step")
        return self


class ScenarioVariant(BaseModel):
    scenario: Scenario
    variant: int
    steps: list[Step]

    @property
    def sample_id(self) -> str:
        return self.scenario.id if self.variant == 0 else f"{self.scenario.id}~{self.variant}"


def expand_variants(scenario: Scenario) -> list[ScenarioVariant]:
    base: list[Step] = [
        s.model_copy(update={"variants": []}) if isinstance(s, UserStep) else s
        for s in scenario.steps
    ]
    out = [ScenarioVariant(scenario=scenario, variant=0, steps=base)]
    for index, step in enumerate(scenario.steps):
        if isinstance(step, UserStep) and step.variants:
            for n, text in enumerate(step.variants, start=1):
                steps = list(base)
                steps[index] = base[index].model_copy(update={"user": text})
                out.append(ScenarioVariant(scenario=scenario, variant=n, steps=steps))
    return out


def load_scenario(path: Path, suite: str) -> Scenario:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path}: expected a mapping at the top level")
    try:
        scenario = Scenario.model_validate({**raw, "suite": suite, "path": str(path)})
    except ValidationError as exc:
        raise ScenarioError(f"{path}: {exc}") from exc
    if not scenario.id.startswith(f"{suite}."):
        raise ScenarioError(f"{path}: id {scenario.id!r} must start with '{suite}.'")
    return scenario


def available_suites(root: Path = SUITES_DIR) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.glob("*.yaml")))


def load_suites(names: Sequence[str] | None = None, root: Path = SUITES_DIR) -> dict[str, list[Scenario]]:
    known = available_suites(root)
    wanted = list(names) if names else known
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise ScenarioError(f"unknown suite(s) {unknown}; available: {', '.join(known)}")
    seen: dict[str, str] = {}
    out: dict[str, list[Scenario]] = {}
    for name in wanted:
        scenarios = [load_scenario(p, name) for p in sorted((root / name).glob("*.yaml"))]
        for s in scenarios:
            if s.id in seen:
                raise ScenarioError(f"{s.path}: duplicate id {s.id!r} (also in {seen[s.id]})")
            seen[s.id] = s.path
        out[name] = scenarios
    return out


def select(
    scenarios: Iterable[Scenario], *, tags: Sequence[str] = (), include_pending: bool = False
) -> list[Scenario]:
    return [
        s
        for s in scenarios
        if (include_pending or s.status == "shipped") and (not tags or set(tags) & set(s.tags))
    ]
```

- [ ] **Step 4: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_scenario.py -v && .venv/bin/mypy --strict evals/`
Expected: PASS and clean.

- [ ] **Step 5: Commit**

```bash
git add evals/harness/scenario.py tests/evals/harness/test_scenario.py
git commit -m "feat(evals): golden scenario schema, loader and variants"
```

---

### Task 5: The apartment world and the fake Home Assistant

**Files:**
- Create: `evals/harness/world.py`, `evals/harness/worlds/apartment.yaml`, `evals/harness/fake_ha.py`
- Test: `tests/evals/harness/test_world.py`, `tests/evals/harness/test_fake_ha.py`

**Interfaces:**
- Consumes: `HaCall` and `HaState` (Task 3).
- Produces:
  - `World` with `.initial_states() -> dict[str, HaState]`, `.entity_registry()`, `.device_registry()`, `.area_registry()`, `.area_of(entity_id)` and `.entity_ids_in_area(area_id, domain)`.
  - `load_world(name) -> World`.
  - `FakeHA(world, *, token=EVAL_HA_TOKEN, host="127.0.0.1", port=0)` with:
    - lifecycle: `async start()`, `async stop()`
    - attributes: `.url`, `.port`, `.calls: list[HaCall]`, `.connected: asyncio.Event`
    - methods: `reset()`, `states() -> dict[str, HaState]`, `calls_between(t0, t1)`, `async set_state(entity_id, state, attributes=None)`, `async restore_world() -> int`
  - `apply_service(states, domain, service, data, entity_ids) -> dict[str, tuple[HaState, HaState]]`.
  - `EVAL_HA_TOKEN = "alfred-eval-ha-token"`.
- **Wire protocol** (it matches home-service's `app/ha_connection.py`):
  - handshake: `auth_required` → `auth{access_token}` → `auth_ok`, or `auth_invalid`
  - commands: `subscribe_events`, the three `config/*_registry/list` commands, `get_states`, `get_services`, `call_service`
  - every result carries `id`, and we never send a JSON array frame
  - `connected` is set once `get_services` has been served

- [ ] **Step 1: Write the world fixture**

```yaml
# evals/harness/worlds/apartment.yaml
# A fictional two-bedroom apartment. Every name here is invented.
name: apartment
areas:
  - {area_id: living_room, name: Living Room}
  - {area_id: bedroom, name: Bedroom}
  - {area_id: kitchen, name: Kitchen}
  - {area_id: entryway, name: Entryway}
  - {area_id: garage, name: Garage}
devices:
  - {id: dev_living_tv, name: Living Room TV, area_id: living_room}
  - {id: dev_front_lock, name: Front Door Lock, area_id: entryway}
  - {id: dev_garage_opener, name: Garage Door Opener, area_id: garage}
entities:
  - {entity_id: light.living_room_lamp, name: Living Room Lamp, area_id: living_room, state: "on", attributes: {brightness: 180}}
  - {entity_id: light.living_room_ceiling, name: Living Room Ceiling, area_id: living_room, state: "off"}
  - {entity_id: light.bedroom_lamp, name: Bedroom Lamp, area_id: bedroom, state: "off"}
  - {entity_id: light.kitchen_pendants, name: Kitchen Pendants, area_id: kitchen, state: "on", attributes: {brightness: 255}}
  - {entity_id: switch.coffee_maker, name: Coffee Maker, area_id: kitchen, state: "off"}
  - {entity_id: media_player.living_room_tv, name: Living Room TV, area_id: living_room, device_id: dev_living_tv, state: "off", attributes: {volume_level: 0.35}}
  - {entity_id: scene.movie_night, name: Movie Night, area_id: living_room, state: "2026-01-01T00:00:00+00:00"}
  - {entity_id: scene.good_night, name: Good Night, state: "2026-01-01T00:00:00+00:00"}
  - {entity_id: lock.front_door, name: Front Door Lock, area_id: entryway, device_id: dev_front_lock, state: locked}
  - {entity_id: cover.garage_door, name: Garage Door, area_id: garage, device_id: dev_garage_opener, state: closed, attributes: {device_class: garage}}
  - {entity_id: alarm_control_panel.home_alarm, name: Home Alarm, area_id: entryway, state: disarmed}
  - {entity_id: binary_sensor.front_door, name: Front Door, area_id: entryway, state: "off", attributes: {device_class: door}}
  - {entity_id: binary_sensor.living_room_motion, name: Living Room Motion, area_id: living_room, state: "off", attributes: {device_class: motion}}
  - {entity_id: sensor.living_room_temperature, name: Living Room Temperature, area_id: living_room, state: "21.5", attributes: {device_class: temperature, unit_of_measurement: "°C"}}
  - {entity_id: light.hallway_old, name: Old Hallway Light, area_id: entryway, state: "off", disabled: true}
services:
  light:
    turn_on:
      name: Turn on
      description: Turn on one or more lights.
      fields:
        brightness_pct: {name: Brightness, description: Brightness percentage., example: 50, selector: {number: {min: 0, max: 100}}}
      target: {entity: [{}]}
    turn_off: {name: Turn off, description: Turn off one or more lights., fields: {}, target: {entity: [{}]}}
    toggle: {name: Toggle, description: Toggle one or more lights., fields: {}, target: {entity: [{}]}}
  switch:
    turn_on: {name: Turn on, description: Turn a switch on., fields: {}, target: {entity: [{}]}}
    turn_off: {name: Turn off, description: Turn a switch off., fields: {}, target: {entity: [{}]}}
  media_player:
    turn_on: {name: Turn on, description: Turn on a media player., fields: {}, target: {entity: [{}]}}
    turn_off: {name: Turn off, description: Turn off a media player., fields: {}, target: {entity: [{}]}}
    media_play: {name: Play, description: Start playing., fields: {}, target: {entity: [{}]}}
    media_pause: {name: Pause, description: Pause playback., fields: {}, target: {entity: [{}]}}
    volume_set:
      name: Set volume
      description: Set a media player's volume.
      fields:
        volume_level: {name: Level, description: Volume from 0 to 1., example: 0.4, selector: {number: {min: 0, max: 1, step: 0.01}}}
      target: {entity: [{}]}
  scene:
    turn_on: {name: Activate, description: Activate a scene., fields: {}, target: {entity: [{}]}}
  lock:
    lock:
      name: Lock
      description: Lock a lock.
      fields: {code: {name: Code, description: Lock code., selector: {text: {}}}}
      target: {entity: [{}]}
    unlock:
      name: Unlock
      description: Unlock a lock.
      fields: {code: {name: Code, description: Lock code., selector: {text: {}}}}
      target: {entity: [{}]}
  cover:
    open_cover: {name: Open, description: Open a cover., fields: {}, target: {entity: [{}]}}
    close_cover: {name: Close, description: Close a cover., fields: {}, target: {entity: [{}]}}
  alarm_control_panel:
    alarm_arm_away:
      name: Arm away
      description: Arm the alarm in away mode.
      fields: {code: {name: Code, description: Alarm code., selector: {text: {}}}}
      target: {entity: [{}]}
    alarm_disarm:
      name: Disarm
      description: Disarm the alarm.
      fields: {code: {name: Code, description: Alarm code., selector: {text: {}}}}
      target: {entity: [{}]}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/evals/harness/test_world.py
from __future__ import annotations

from evals.harness.world import load_world


def test_apartment_registries_have_the_fields_home_service_requires() -> None:
    world = load_world("apartment")
    for e in world.entity_registry():
        assert {"entity_id", "area_id", "device_id", "name", "original_name", "disabled_by"} <= set(e)
    for d in world.device_registry():
        assert {"id", "area_id", "name", "name_by_user"} <= set(d)
    for a in world.area_registry():
        assert {"area_id", "name"} <= set(a)


def test_disabled_entities_are_registered_but_have_no_state() -> None:
    world = load_world("apartment")
    reg = {e["entity_id"]: e for e in world.entity_registry()}
    assert reg["light.hallway_old"]["disabled_by"] == "user"
    assert "light.hallway_old" not in world.initial_states()


def test_states_carry_friendly_names() -> None:
    states = load_world("apartment").initial_states()
    assert states["light.bedroom_lamp"].attributes["friendly_name"] == "Bedroom Lamp"
    assert states["light.bedroom_lamp"].state == "off"


def test_entities_in_area_by_domain() -> None:
    world = load_world("apartment")
    assert world.entity_ids_in_area("living_room", "light") == [
        "light.living_room_ceiling",
        "light.living_room_lamp",
    ]
```

```python
# tests/evals/harness/test_fake_ha.py
from __future__ import annotations

import json
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect

from evals.harness.evidence import HaState
from evals.harness.fake_ha import EVAL_HA_TOKEN, FakeHA, apply_service
from evals.harness.world import load_world


async def handshake(ha: FakeHA, token: str = EVAL_HA_TOKEN) -> tuple[ClientConnection, dict[str, Any]]:
    ws = await connect(ha.url.replace("http", "ws") + "/api/websocket")
    assert json.loads(await ws.recv())["type"] == "auth_required"
    await ws.send(json.dumps({"type": "auth", "access_token": token}))
    return ws, json.loads(await ws.recv())


async def command(ws: ClientConnection, msg_id: int, **payload: Any) -> dict[str, Any]:
    await ws.send(json.dumps({"id": msg_id, **payload}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("type") == "result" and msg["id"] == msg_id:
            return msg


@pytest.fixture
async def ha():  # type: ignore[no-untyped-def]
    server = FakeHA(load_world("apartment"))
    await server.start()
    yield server
    await server.stop()


async def test_rejects_a_bad_token(ha: FakeHA) -> None:
    ws, reply = await handshake(ha, token="wrong")
    assert reply["type"] == "auth_invalid"
    await ws.close()


async def test_serves_home_service_setup_and_marks_connected(ha: FakeHA) -> None:
    ws, reply = await handshake(ha)
    assert reply["type"] == "auth_ok"
    assert (await command(ws, 1, type="subscribe_events", event_type="state_changed"))["success"]
    states = (await command(ws, 2, type="get_states"))["result"]
    assert {s["entity_id"] for s in states} >= {"light.bedroom_lamp", "lock.front_door"}
    assert not ha.connected.is_set()
    services = (await command(ws, 3, type="get_services"))["result"]
    assert "turn_on" in services["light"]
    assert ha.connected.is_set()
    await ws.close()


async def test_call_service_on_an_area_records_entities_and_pushes_state(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    await command(ws, 1, type="subscribe_events", event_type="state_changed")
    result = await command(
        ws, 2, type="call_service", domain="light", service="turn_on",
        service_data={"brightness_pct": 50}, target={"area_id": "bedroom"},
    )
    assert result["success"]
    assert ha.calls[-1].entity_ids == ["light.bedroom_lamp"]
    assert ha.states()["light.bedroom_lamp"] == HaState(
        state="on", attributes={"friendly_name": "Bedroom Lamp", "brightness": 128}
    )
    event = json.loads(await ws.recv())
    assert event["id"] == 1 and event["event"]["data"]["new_state"]["state"] == "on"
    await ws.close()


async def test_unknown_service_is_an_error_and_not_recorded(ha: FakeHA) -> None:
    ws, _ = await handshake(ha)
    result = await command(ws, 1, type="call_service", domain="fan", service="turn_on",
                           target={"entity_id": "fan.bathroom"})
    assert result["success"] is False and ha.calls == []
    await ws.close()


async def test_set_state_and_restore_world() -> None:
    ha = FakeHA(load_world("apartment"))
    await ha.set_state("light.living_room_ceiling", "on", {"brightness": 200})
    assert ha.states()["light.living_room_ceiling"].state == "on"
    assert await ha.restore_world() == 1
    assert ha.states()["light.living_room_ceiling"].state == "off"
    with pytest.raises(KeyError):
        await ha.set_state("light.nope", "on")


def test_apply_service_effects() -> None:
    states = {
        "light.a": HaState(state="on", attributes={"brightness": 255}),
        "switch.b": HaState(state="off"),
        "media_player.c": HaState(state="off"),
    }
    changed = apply_service(states, "light", "turn_off", {}, ["light.a"])
    assert changed["light.a"][1] == HaState(state="off")
    apply_service(states, "switch", "toggle", {}, ["switch.b"])
    assert states["switch.b"].state == "on"
    apply_service(states, "media_player", "volume_set", {"volume_level": 0.2}, ["media_player.c"])
    assert states["media_player.c"].attributes["volume_level"] == 0.2
```

- [ ] **Step 3: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_world.py tests/evals/harness/test_fake_ha.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `world.py`**

```python
# evals/harness/world.py
"""World fixtures: the home the fake Home Assistant serves (evals/harness/worlds/)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.harness.evidence import HaState

WORLDS_DIR = Path(__file__).resolve().parent / "worlds"


class WorldArea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    area_id: str
    name: str


class WorldDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    area_id: str | None = None


class WorldEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    name: str
    area_id: str | None = None
    device_id: str | None = None
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    disabled: bool = False

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


class World(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    areas: list[WorldArea]
    devices: list[WorldDevice] = Field(default_factory=list)
    entities: list[WorldEntity]
    services: dict[str, dict[str, dict[str, Any]]]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        areas = {a.area_id for a in self.areas}
        devices = {d.id for d in self.devices}
        ids = [e.entity_id for e in self.entities]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate entity ids")
        for e in self.entities:
            if e.area_id is not None and e.area_id not in areas:
                raise ValueError(f"{e.entity_id}: unknown area {e.area_id}")
            if e.device_id is not None and e.device_id not in devices:
                raise ValueError(f"{e.entity_id}: unknown device {e.device_id}")
        return self

    def initial_states(self) -> dict[str, HaState]:
        return {
            e.entity_id: HaState(state=e.state, attributes={"friendly_name": e.name, **e.attributes})
            for e in self.entities
            if not e.disabled
        }

    def entity_registry(self) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": e.entity_id,
                "area_id": e.area_id,
                "device_id": e.device_id,
                "name": None,
                "original_name": e.name,
                "disabled_by": "user" if e.disabled else None,
            }
            for e in self.entities
        ]

    def device_registry(self) -> list[dict[str, Any]]:
        return [
            {"id": d.id, "area_id": d.area_id, "name": d.name, "name_by_user": None}
            for d in self.devices
        ]

    def area_registry(self) -> list[dict[str, Any]]:
        return [{"area_id": a.area_id, "name": a.name} for a in self.areas]

    def area_of(self, entity_id: str) -> str | None:
        return next((e.area_id for e in self.entities if e.entity_id == entity_id), None)

    def entity_ids_in_area(self, area_id: str, domain: str) -> list[str]:
        return sorted(
            e.entity_id
            for e in self.entities
            if e.area_id == area_id and e.domain == domain and not e.disabled
        )


def load_world(name: str, root: Path = WORLDS_DIR) -> World:
    path = root / f"{name}.yaml"
    if not path.is_file():
        known = sorted(p.stem for p in root.glob("*.yaml"))
        raise FileNotFoundError(f"no world {name!r} in {root}; known: {known}")
    return World.model_validate(yaml.safe_load(path.read_text()))
```

- [ ] **Step 5: Implement `fake_ha.py`**

```python
# evals/harness/fake_ha.py
"""A Home Assistant WebSocket API double for home-service, serving one World.

Speaks the subset home-service uses (alfred-home-service ``app/ha_connection.py``):
auth, subscribe_events, the three registry lists, get_states, get_services and
call_service. A served call_service is recorded, applied to the fake's state, and
pushed back as ``state_changed`` — so Alfred's live state follows, as with a real HA.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve

from evals.harness.evidence import HaCall, HaState
from evals.harness.world import World

logger = logging.getLogger(__name__)

EVAL_HA_TOKEN = "alfred-eval-ha-token"
_HA_VERSION = "2026.7.0"

_SET_STATE: dict[tuple[str, str], str] = {
    ("light", "turn_on"): "on",
    ("light", "turn_off"): "off",
    ("switch", "turn_on"): "on",
    ("switch", "turn_off"): "off",
    ("media_player", "turn_on"): "on",
    ("media_player", "turn_off"): "off",
    ("media_player", "media_play"): "playing",
    ("media_player", "media_pause"): "paused",
    ("lock", "lock"): "locked",
    ("lock", "unlock"): "unlocked",
    ("cover", "open_cover"): "open",
    ("cover", "close_cover"): "closed",
    ("alarm_control_panel", "alarm_arm_away"): "armed_away",
    ("alarm_control_panel", "alarm_disarm"): "disarmed",
}


def apply_service(
    states: dict[str, HaState],
    domain: str,
    service: str,
    data: dict[str, Any],
    entity_ids: list[str],
) -> dict[str, tuple[HaState, HaState]]:
    """Apply a service call to *states* in place; return {entity_id: (old, new)}."""
    changed: dict[str, tuple[HaState, HaState]] = {}
    for entity_id in entity_ids:
        old = states.get(entity_id)
        if old is None:
            continue
        attrs = dict(old.attributes)
        new_state = old.state
        if service == "toggle":
            new_state = "off" if old.state == "on" else "on"
        elif (domain, service) in _SET_STATE:
            new_state = _SET_STATE[(domain, service)]
        if domain == "light" and new_state == "on":
            if "brightness_pct" in data:
                attrs["brightness"] = round(float(data["brightness_pct"]) * 255 / 100)
            elif "brightness" in data:
                attrs["brightness"] = int(data["brightness"])
        if domain == "light" and new_state == "off":
            attrs.pop("brightness", None)
        if domain == "media_player" and "volume_level" in data:
            attrs["volume_level"] = float(data["volume_level"])
        new = HaState(state=new_state, attributes=attrs)
        if new != old:
            states[entity_id] = new
            changed[entity_id] = (old, new)
    return changed


class FakeHA:
    def __init__(
        self,
        world: World,
        *,
        token: str = EVAL_HA_TOKEN,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.world = world
        self.token = token
        self.host = host
        self._port = port
        self.calls: list[HaCall] = []
        self.connected = asyncio.Event()
        self._states = world.initial_states()
        self._subs: dict[ServerConnection, dict[str, int]] = {}
        self._server: Server | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._port}"

    async def start(self) -> None:
        self._server = await serve(self._handler, self.host, self._port)
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def reset(self) -> None:
        """Back to the world's initial state, for a new container."""
        self._states = self.world.initial_states()
        self.calls.clear()
        self.connected.clear()

    def states(self) -> dict[str, HaState]:
        return {k: v.model_copy(deep=True) for k, v in self._states.items()}

    def calls_between(self, t0: float, t1: float) -> list[HaCall]:
        return [c for c in self.calls if t0 <= c.t <= t1]

    async def set_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> None:
        old = self._states.get(entity_id)
        if old is None:
            raise KeyError(f"unknown entity {entity_id!r} in world {self.world.name!r}")
        new = HaState(state=state, attributes={**old.attributes, **(attributes or {})})
        self._states[entity_id] = new
        await self._push_change(entity_id, old, new)

    async def restore_world(self) -> int:
        """Push every entity that drifted from the world back to it. Returns the count."""
        restored = 0
        for entity_id, initial in self.world.initial_states().items():
            current = self._states.get(entity_id)
            if current != initial:
                self._states[entity_id] = initial
                if current is not None:
                    await self._push_change(entity_id, current, initial)
                restored += 1
        return restored

    def _targets(self, domain: str, target: dict[str, Any] | None) -> list[str]:
        if not target:
            return []
        ids: list[str] = []
        raw_ids = target.get("entity_id", [])
        ids += [raw_ids] if isinstance(raw_ids, str) else list(raw_ids)
        raw_areas = target.get("area_id", [])
        for area in [raw_areas] if isinstance(raw_areas, str) else list(raw_areas):
            ids += self.world.entity_ids_in_area(area, domain)
        return sorted(dict.fromkeys(ids))

    async def _handler(self, ws: ServerConnection) -> None:
        self._subs[ws] = {}
        try:
            await ws.send(json.dumps({"type": "auth_required", "ha_version": _HA_VERSION}))
            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth" or msg.get("access_token") != self.token:
                await ws.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))
                return
            await ws.send(json.dumps({"type": "auth_ok", "ha_version": _HA_VERSION}))
            async for raw in ws:
                await self._command(ws, json.loads(raw))
        except Exception:  # noqa: BLE001 — a dropped client is not the harness's failure
            logger.debug("fake HA connection closed", exc_info=True)
        finally:
            self._subs.pop(ws, None)

    async def _command(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        msg_id = int(msg["id"])
        match msg.get("type"):
            case "subscribe_events":
                self._subs[ws][str(msg.get("event_type", "*"))] = msg_id
                await self._result(ws, msg_id, None)
            case "config/entity_registry/list":
                await self._result(ws, msg_id, self.world.entity_registry())
            case "config/device_registry/list":
                await self._result(ws, msg_id, self.world.device_registry())
            case "config/area_registry/list":
                await self._result(ws, msg_id, self.world.area_registry())
            case "get_states":
                states = [
                    {"entity_id": k, "state": v.state, "attributes": v.attributes}
                    for k, v in self._states.items()
                ]
                await self._result(ws, msg_id, states)
            case "get_services":
                # Set before replying: once the reply is out, a waiter may already look.
                self.connected.set()
                await self._result(ws, msg_id, self.world.services)
            case "call_service":
                await self._call_service(ws, msg_id, msg)
            case other:
                await self._error(ws, msg_id, "unknown_command", f"Unknown command: {other}")

    async def _call_service(self, ws: ServerConnection, msg_id: int, msg: dict[str, Any]) -> None:
        domain, service = str(msg.get("domain")), str(msg.get("service"))
        if service not in self.world.services.get(domain, {}):
            await self._error(ws, msg_id, "service_not_found", f"{domain}.{service} not found")
            return
        data = dict(msg.get("service_data") or {})
        entity_ids = self._targets(domain, msg.get("target"))
        self.calls.append(
            HaCall(t=time.monotonic(), domain=domain, service=service, service_data=data,
                   entity_ids=entity_ids)
        )
        changed = apply_service(self._states, domain, service, data, entity_ids)
        await self._result(ws, msg_id, {"context": {"id": f"eval-{msg_id}"}, "response": None})
        for entity_id, (old, new) in changed.items():
            await self._push_change(entity_id, old, new)

    async def _push_change(self, entity_id: str, old: HaState, new: HaState) -> None:
        def as_state(s: HaState) -> dict[str, Any]:
            return {"entity_id": entity_id, "state": s.state, "attributes": s.attributes}

        for ws, subs in list(self._subs.items()):
            sub_id = subs.get("state_changed")
            if sub_id is None:
                continue
            event = {
                "id": sub_id,
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {"entity_id": entity_id, "old_state": as_state(old), "new_state": as_state(new)},
                },
            }
            await ws.send(json.dumps(event))

    async def _result(self, ws: ServerConnection, msg_id: int, result: Any) -> None:
        await ws.send(json.dumps({"id": msg_id, "type": "result", "success": True, "result": result}))

    async def _error(self, ws: ServerConnection, msg_id: int, code: str, message: str) -> None:
        await ws.send(json.dumps({
            "id": msg_id, "type": "result", "success": False,
            "error": {"code": code, "message": message},
        }))
```

- [ ] **Step 6: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_world.py tests/evals/harness/test_fake_ha.py -v && .venv/bin/mypy --strict evals/`
Expected: PASS and clean.

- [ ] **Step 7: Commit**

```bash
git add evals/harness/world.py evals/harness/worlds evals/harness/fake_ha.py tests/evals/harness/test_world.py tests/evals/harness/test_fake_ha.py
git commit -m "feat(evals): apartment world and fake Home Assistant"
```

---

### Task 6: The LLM recorder proxy

**Files:**
- Create: `evals/harness/proxy.py`
- Test: `tests/evals/harness/test_proxy.py`

**Interfaces:**
- Consumes: `LlmCall`, `ToolCall` and `Role` (Task 3).
- Produces:
  - `LlmProxy(upstream, *, host="127.0.0.1", port=0, max_concurrency=4, timeout_s=300.0, transport=None)` with `async start()`, `async stop()`, `.url`, `.port`, `.calls: list[LlmCall]` and `calls_between(t0, t1)`.
  - `classify_role(messages) -> Role`.
  - `ROLE_FINGERPRINTS`.
- **Endpoints and roles:**
  - `upstream` is the vLLM origin **without** `/v1`.
  - `POST /v1/chat/completions` is recorded. Every other path is passed through and not recorded.
  - A streaming request is refused with 400.
  - Roles come from fingerprints in the first message.

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/harness/test_proxy.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from evals.harness.proxy import ROLE_FINGERPRINTS, LlmProxy, classify_role

REPO = Path(__file__).resolve().parents[3]

COMPLETION = {
    "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "home_light_turn_on", "arguments": "{\"target\": \"Bedroom Lamp\"}"}}]}}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 7},
}


def upstream(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


@pytest.fixture
async def proxy():  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", transport=upstream(handle))
    await p.start()
    p.seen = seen  # type: ignore[attr-defined]
    yield p
    await p.stop()


async def test_records_a_system2_tool_call(proxy: LlmProxy) -> None:
    body = {"model": "m", "messages": [{"role": "system", "content": "You are Alfred — personal butler and assistant to sir."}],
            "tools": [{"type": "function", "function": {"name": "home_light_turn_on", "parameters": {}}}]}
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{proxy.url}/v1/chat/completions", json=body)
    assert r.json() == COMPLETION
    [call] = proxy.calls
    assert call.role == "system2" and call.tools_offered == ["home_light_turn_on"]
    assert call.tool_calls[0].arguments == {"target": "Bedroom Lamp"}
    assert call.prompt_tokens == 100 and call.status == 200
    assert json.loads(proxy.seen[0].content) == body  # type: ignore[attr-defined]


async def test_passthrough_is_not_recorded(proxy: LlmProxy) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{proxy.url}/v1/models")
    assert r.json()["data"][0]["id"] == "m" and proxy.calls == []


async def test_streaming_is_refused(proxy: LlmProxy) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{proxy.url}/v1/chat/completions",
                              json={"model": "m", "messages": [], "stream": True})
    assert r.status_code == 400 and proxy.seen == []  # type: ignore[attr-defined]


async def test_upstream_failure_is_recorded_as_502() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    p = LlmProxy("http://vllm.test", transport=upstream(boom))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []})
        assert r.status_code == 502 and p.calls[0].status == 502
    finally:
        await p.stop()


async def test_concurrency_cap() -> None:
    active = 0
    peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return httpx.Response(200, json=COMPLETION)

    p = LlmProxy("http://vllm.test", max_concurrency=2, transport=httpx.MockTransport(slow))
    await p.start()
    try:
        async with httpx.AsyncClient() as client:
            await asyncio.gather(*[
                client.post(f"{p.url}/v1/chat/completions", json={"model": "m", "messages": []})
                for _ in range(6)
            ])
        assert peak == 2
    finally:
        await p.stop()


@pytest.mark.parametrize(("text", "role"), [
    ("You are Alfred's Reflex Engine — a fast-acting steward for a smart home.", "system1"),
    ("You are Alfred — personal butler and assistant to sir.\n...", "system2"),
    ("You are a pattern analyst for ...", "librarian"),
    ("hello", "unknown"),
])
def test_classify_role(text: str, role: str) -> None:
    assert classify_role([{"role": "system", "content": text}]) == role
    assert classify_role([{"role": "user", "content": [{"type": "text", "text": text}]}]) == role


def test_every_fingerprint_still_exists_in_the_prompts() -> None:
    sources = "".join(
        (REPO / p).read_text()
        for p in ("core/conscious/prompts/personality.md", "core/reflex/engine.py",
                  "core/librarian/consolidator.py")
    )
    for _, fingerprint in ROLE_FINGERPRINTS:
        assert fingerprint in sources, f"prompt changed; update ROLE_FINGERPRINTS: {fingerprint!r}"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_proxy.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# evals/harness/proxy.py
"""An OpenAI-compatible pass-through to vLLM that records every chat completion.

System 2 sends its tool calls to the model and to home-service, never onto a stream, so
this is the only place their arguments can be seen. Roles are told apart by the first
message's text (``ROLE_FINGERPRINTS``); a test pins those strings to the prompt sources.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
from aiohttp import web

from evals.harness.evidence import LlmCall, Role, ToolCall

ROLE_FINGERPRINTS: tuple[tuple[Role, str], ...] = (
    ("system1", "You are Alfred's Reflex Engine"),
    ("system2", "You are Alfred — personal butler"),
    ("librarian", "You are a memory analyst"),
    ("librarian", "You are a memory conflict resolver"),
    ("librarian", "Analyze these home assistant observations"),
    ("librarian", "You are a memory compressor"),
    ("librarian", "You are a pattern analyst"),
)
_DROP_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def classify_role(messages: list[dict[str, Any]]) -> Role:
    first = _text(messages[0].get("content")) if messages else ""
    for role, fingerprint in ROLE_FINGERPRINTS:
        if fingerprint in first:
            return role
    return "unknown"


def _parse(payload: dict[str, Any]) -> tuple[str | None, list[ToolCall], int | None, int | None]:
    choices = payload.get("choices") or [{}]
    message = choices[0].get("message") or {}
    calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            args = {"_raw": raw}
        calls.append(ToolCall(name=str(fn.get("name", "")), arguments=args))
    usage = payload.get("usage") or {}
    return message.get("content"), calls, usage.get("prompt_tokens"), usage.get("completion_tokens")


class LlmProxy:
    def __init__(
        self,
        upstream: str,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        max_concurrency: int = 4,
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.upstream = upstream.rstrip("/")
        self.host = host
        self._port = port
        self.calls: list[LlmCall] = []
        self._sem = asyncio.Semaphore(max_concurrency)
        self._timeout_s = timeout_s
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._runner: web.AppRunner | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._port}"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport)
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_post("/v1/chat/completions", self._chat)
        app.router.add_route("*", "/{tail:.*}", self._passthrough)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self._port).start()
        self._port = int(self._runner.addresses[0][1])

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def calls_between(self, t0: float, t1: float) -> list[LlmCall]:
        return [c for c in self.calls if t0 <= c.t <= t1]

    def _headers(self, request: web.Request) -> dict[str, str]:
        return {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}

    async def _chat(self, request: web.Request) -> web.Response:
        assert self._client is not None
        body: dict[str, Any] = await request.json()
        if body.get("stream"):
            return web.json_response(
                {"error": {"message": "the alfred evals proxy does not support streaming"}},
                status=400,
            )
        messages = list(body.get("messages") or [])
        tools = [str(t.get("function", {}).get("name", "")) for t in body.get("tools") or []]
        t = time.monotonic()
        async with self._sem:
            started = time.monotonic()
            try:
                upstream = await self._client.post(
                    f"{self.upstream}/v1/chat/completions", json=body, headers=self._headers(request)
                )
            except httpx.HTTPError as exc:
                self.calls.append(LlmCall(
                    t=t, role=classify_role(messages), latency_ms=(time.monotonic() - started) * 1000,
                    status=502, messages=messages, tools_offered=tools,
                ))
                return web.json_response({"error": {"message": f"upstream failed: {exc}"}}, status=502)
        latency_ms = (time.monotonic() - started) * 1000
        try:
            payload = upstream.json()
        except ValueError:
            payload = {}
        text, tool_calls, prompt_tokens, completion_tokens = _parse(payload)
        self.calls.append(LlmCall(
            t=t, role=classify_role(messages), latency_ms=latency_ms, status=upstream.status_code,
            messages=messages, tools_offered=tools, response_text=text, tool_calls=tool_calls,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        ))
        return web.Response(body=upstream.content, status=upstream.status_code,
                            content_type="application/json")

    async def _passthrough(self, request: web.Request) -> web.Response:
        assert self._client is not None
        upstream = await self._client.request(
            request.method,
            f"{self.upstream}{request.rel_url}",
            content=await request.read(),
            headers=self._headers(request),
        )
        return web.Response(body=upstream.content, status=upstream.status_code,
                            content_type=upstream.headers.get("content-type", "application/json").split(";")[0])
```

- [ ] **Step 4: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_proxy.py -v && .venv/bin/mypy --strict evals/`
Expected: PASS and clean. If a fingerprint test fails, the prompt source has changed: update `ROLE_FINGERPRINTS` to the current first line, and keep the test.

- [ ] **Step 5: Commit**

```bash
git add evals/harness/proxy.py tests/evals/harness/test_proxy.py
git commit -m "feat(evals): LLM recorder proxy in front of vLLM"
```

---

### Task 7: `alfredctl up --eval`, the eval Redis bind, and the home-service override

**Files:**
- Modify:
  - `alfredctl/runtime.py`: add `eval_container_name()`.
  - `alfredctl/launch.py`: `build_plan(..., eval_mode=False)`, `_env_pairs(..., defaults=None)`, constants.
  - `alfredctl/main.py`: `up --eval`.
  - `alfredctl/staging.py`: the `ALFRED_HOME_SERVICE_DIR` override.
  - `runner/__main__.py`: `_redis_command`.
  - `docs/containerization.md`.
- Test: `tests/alfredctl/test_launch.py`, `tests/alfredctl/test_staging.py`, `tests/runner/test_service_list.py`

**Interfaces:**
- Produces:
  - `alfredctl.runtime.eval_container_name() -> str`, which returns `f"alfred-eval-{branch_slug()}"`.
  - `alfredctl.launch.EVAL_SECRETS_PASSPHRASE = "alfred-eval-not-a-secret"`.
  - `alfredctl.launch.EVAL_ENV = {"ALFRED_EVAL": "1", "DAILY_COST_CAP_USD": "1000000"}`.
  - The CLI `alfredctl up --eval --persist DIR [--env K=V ...] [--no-build] [--runtime docker]`.
  - The env `ALFRED_HOME_SERVICE_DIR`, read by `staging.home_service_dir()`.
- **Eval-mode behaviour:**
  - The container is named `alfred-eval-<slug>`.
  - Ports `127.0.0.1::8081` and `127.0.0.1::6379` are published, loopback only on random host ports.
  - `/data` is mounted from `--persist`.
  - `.env` is never read, the doctor preflight is skipped, and the passphrase is fixed.
  - `--env` still overrides `EVAL_ENV`.
  - Redis binds `0.0.0.0`, protected mode off, no persistence.

- [ ] **Step 1: Write the failing tests**

Extend the `_plan` helper in `tests/alfredctl/test_launch.py` with an `eval_mode: bool = False` keyword and pass it through to `build_plan(..., eval_mode=eval_mode)`. Then add:

```python
def test_eval_mode_publishes_loopback_random_ports_for_web_and_redis() -> None:
    args = _plan(mode="persistent", persist=Path("/tmp/eval"), eval_mode=True).run_args
    assert "127.0.0.1::8081" in args and "127.0.0.1::6379" in args
    assert "8081:8081" not in args


def test_eval_mode_names_the_container_apart() -> None:
    assert _plan(mode="persistent", persist=Path("/tmp/eval"), eval_mode=True).name.startswith("alfred-eval-")


def test_eval_mode_mounts_data_sets_eval_flag_and_lifts_cost_cap() -> None:
    args = _plan(mode="persistent", persist=Path("/tmp/eval"), eval_mode=True).run_args
    assert "/tmp/eval:/data" in args
    assert "ALFRED_EVAL=1" in args and "DAILY_COST_CAP_USD=1000000" in args


def test_eval_mode_env_flag_still_wins() -> None:
    args = _plan(mode="persistent", persist=Path("/tmp/eval"), eval_mode=True,
                 extra_env=["DAILY_COST_CAP_USD=5"]).run_args
    assert "DAILY_COST_CAP_USD=5" in args and "DAILY_COST_CAP_USD=1000000" not in args


def test_eval_mode_needs_a_persist_dir() -> None:
    with pytest.raises(ValueError, match="persist"):
        _plan(mode="persistent", persist=None, eval_mode=True)


def test_eval_mode_rejects_the_apple_runtime() -> None:
    with pytest.raises(ValueError, match="docker and podman"):
        _plan(rt=APPLE, mode="persistent", persist=Path("/tmp/eval"), eval_mode=True)
```

In `tests/runner/test_service_list.py`, add:

```python
def test_redis_command_eval_binds_all_interfaces_without_persistence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "persistent")
    monkeypatch.setenv("ALFRED_EVAL", "1")
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    cmd = _redis_command(tmp_path / "redis")
    assert cmd[cmd.index("--bind") + 1] == "0.0.0.0"
    assert cmd[cmd.index("--protected-mode") + 1] == "no"
    assert cmd[cmd.index("--appendonly") + 1] == "no"
```

In `tests/alfredctl/test_staging.py`, add:

```python
def test_home_service_dir_honours_the_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_HOME_SERVICE_DIR", str(tmp_path))
    assert staging.home_service_dir() == tmp_path.resolve()


def test_missing_override_dir_is_an_error_not_a_clone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_HOME_SERVICE_DIR", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="ALFRED_HOME_SERVICE_DIR"):
        staging.ensure_home_service()
```

Check how `test_staging.py` imports staging. If it uses `from alfredctl import staging`, use the same.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/alfredctl/test_launch.py tests/alfredctl/test_staging.py tests/runner/test_service_list.py -v`
Expected: the new tests FAIL (`unexpected keyword argument 'eval_mode'`, missing override, bind 127.0.0.1); the existing ones pass.

- [ ] **Step 3: Implement**

`alfredctl/runtime.py`, under `container_name()`:

```python
def eval_container_name() -> str:
    """Container `alfredctl up --eval` starts — apart from this branch's dev container."""
    return f"alfred-eval-{branch_slug()}"
```

`alfredctl/launch.py`:
- Add `eval_container_name` to the runtime import.
- Add the constants below the imports:

```python
# `alfred evals` stacks: throwaway, driven from the host, never holding a real secret.
EVAL_SECRETS_PASSPHRASE = "alfred-eval-not-a-secret"
# Unknown models are priced at the default rate (core/conscious/cost.py), so a local
# model under eval would trip the $5 default cap mid-run.
EVAL_ENV: dict[str, str] = {"ALFRED_EVAL": "1", "DAILY_COST_CAP_USD": "1000000"}
```

Give `_env_pairs` a `defaults: dict[str, str] | None = None` parameter, and apply it right after the passphrase line:

```python
    merged["ALFRED_DATA_MODE"] = mode
    merged["ALFRED_SECRETS_PASSPHRASE"] = passphrase
    merged.update(defaults or {})
```

Add `eval_mode: bool = False` to `build_plan`, then change its body:

```python
    if eval_mode:
        if rt.name == "container":
            raise ValueError("--eval supports docker and podman")
        if persist is None:
            raise ValueError("--eval needs a persist dir")
    name = eval_container_name() if eval_mode else container_name()
    image = image_tag()
    args = ["run", "--detach", "--name", name]
    if rt.name == "container":
        ...  # unchanged
    else:
        if eval_mode:
            # Loopback-only, host-chosen ports: never clashes with a running stack on
            # 8081, and the harness reaches Redis without exposing it to the LAN.
            args += ["-p", "127.0.0.1::8081", "-p", "127.0.0.1::6379"]
        else:
            args += ["-p", f"{port}:8081"]
        ...  # expose flags and add-host unchanged
    args += ["-v", f"{models}:/models"]
    if hf_cache is not None:
        args += ["-v", f"{hf_cache}:/models/hf"]
    if (mode == "persistent" or eval_mode) and persist is not None:
        args += ["-v", f"{persist}:/data"]
    env_args, notes = _env_pairs(
        rt, mode, env_file, extra_env, passphrase, EVAL_ENV if eval_mode else None
    )
```

`alfredctl/main.py`, `up`. Add the option after `cpus`:

```python
    eval_mode: Annotated[
        bool,
        typer.Option(
            "--eval",
            help="Throwaway stack for `alfred evals`: needs --persist, ignores .env, "
            "loopback-only random ports (8081 and Redis), fixed fake passphrase",
        ),
    ] = False,
```

Validate it right after the mode check:

```python
    if eval_mode:
        if persist is None:
            raise typer.BadParameter("--eval needs --persist DIR (the harness's data dir)")
        if mode != "persistent":
            raise typer.BadParameter("--eval always runs persistent mode on --persist; drop --mode")
```

Then:
- Wrap the preflight block in `if not eval_mode:`. The doctor reads `.env`, which eval mode never uses.
- Change the plan inputs:

```python
    env_file = None if eval_mode else repo / ".env"
    passphrase = launch.EVAL_SECRETS_PASSPHRASE if eval_mode else _passphrase(mode, persist_dir)
    plan = launch.build_plan(
        ...,
        env_file=env_file if env_file is not None and env_file.is_file() else None,
        passphrase=passphrase,
        memory=memory,
        cpus=cpus,
        eval_mode=eval_mode,
    )
```

`alfredctl/staging.py` (`import os` if it is missing):

```python
def home_service_dir() -> Path:
    """The home-service checkout the image build copies.

    Defaults to the sibling repo next to the main checkout. ``ALFRED_HOME_SERVICE_DIR``
    overrides it — ``alfred evals`` pins the version production runs that way.
    """
    override = os.getenv("ALFRED_HOME_SERVICE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return workspace_root() / "home-service"
```

At the top of `ensure_home_service`, after `path = home_service_dir()`:

```python
    if os.getenv("ALFRED_HOME_SERVICE_DIR") and not path.is_dir():
        raise FileNotFoundError(f"ALFRED_HOME_SERVICE_DIR={path} does not exist")
```

`runner/__main__.py`, `_redis_command`. Replace the `cmd = [...]` line and the persistence `if`:

```python
    cmd = ["redis-server", "--dir", str(redis_dir)]
    if is_truthy_flag(os.getenv("ALFRED_EVAL")):
        # `alfred evals` drives the bus from the host through a loopback-only published
        # port, which arrives on the container's eth0; a throwaway stack keeps nothing.
        cmd += ["--bind", "0.0.0.0", "--protected-mode", "no", "--save", "", "--appendonly", "no"]
    else:
        cmd += ["--bind", "127.0.0.1"]
        if data_mode() == "persistent":
            cmd += ["--appendonly", "yes"]
        else:
            cmd += ["--save", "", "--appendonly", "no"]
```

`is_truthy_flag` is already imported in the runner. If it is not, import it from `shared.env`.

`docs/containerization.md`: in the `up` row of the commands table, add `--eval` to the flags. Under the `ALFRED_DATA_MODE` table, add an "Eval mode" paragraph with these points:
- `alfredctl up --eval --persist DIR` starts `alfred-eval-<branch>` for `alfred evals` (docs/evals.md).
- It publishes 8081 and Redis on random `127.0.0.1` ports (`docker port alfred-eval-<branch>`).
- It never reads `.env`, and it uses a fixed fake passphrase.
- It sets `ALFRED_EVAL=1`, which makes Redis listen on the container interface with no persistence, and lifts the cost cap.
- `ALFRED_HOME_SERVICE_DIR` picks which home-service checkout a build copies.

- [ ] **Step 4: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/alfredctl tests/runner -q && .venv/bin/mypy --strict alfredctl/ runner/`
Expected: PASS, including `test_only_8081_published_by_default` unchanged, and clean.

- [ ] **Step 5: Commit**

```bash
git add alfredctl runner tests/alfredctl tests/runner docs/containerization.md
git commit -m "feat(alfredctl): add up --eval for throwaway eval stacks"
```

---

### Task 8: Preflight, network helpers and the Stack

**Files:**
- Create: `evals/harness/net.py`, `evals/harness/preflight.py`, `evals/harness/stack.py`
- Test: `tests/evals/harness/test_preflight.py`, `tests/evals/harness/test_stack.py`

**Interfaces:**
- Consumes:
  - `FakeHA` (Task 5) and `LlmProxy` (Task 6).
  - `eval_container_name` and `image_tag` (Task 7).
  - `publish_and_wait` (`core/channels/request_bus.py`), `create_redis` (`shared/redis_streams.py`), `UserRequest` and `AlfredResponse` (`bus/schemas/events.py`).
- Produces:
  - `net.docker_bridge_gateway() -> str`, `net.in_container_url(port) -> str` and `net.container_reachable(url) -> str`.
  - `preflight.PreflightError`, `async check_models(client, base_url, model) -> None` and `check_home_service(path, *, allow_stale, git=_git) -> str` (returns the commit).
  - `stack.StackConfig`, `stack.StackError`, `stack.container_env(cfg, *, proxy_port, fake_ha_port) -> dict[str, str]`, and the constants `CONSCIOUS_SOURCE`, `EVAL_SOURCE`, `EVAL_SIGNAL_NUMBER` and `EVAL_OPENROUTER_PLACEHOLDER`.
  - `stack.Docker`: the async docker wrapper.
  - `stack.Stack(cfg, *, fake_ha, proxy, docker=None, alfredctl=None, health=None)`:
    - attributes: `.name`, `.image`, `.redis`, `.web_port`, `.redis_port`, `.boot_seconds`, `.first_reply_ms`, `.restarts`
    - methods: `up_command(data_dir) -> list[str]`, `async start()`, `async stop()`, `async restart()`, `async alive() -> bool`, `async send(request, timeout_s) -> AlfredResponse`

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/harness/test_preflight.py
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from evals.harness.preflight import PreflightError, check_home_service, check_models


async def test_check_models_names_what_it_found() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": [{"id": "other-model"}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PreflightError) as err:
            await check_models(client, "http://localhost:8000/v1", "gemma-4-26b-a4b")
    assert "other-model" in str(err.value) and "http://localhost:8000/v1" in str(err.value)


async def test_check_models_unreachable() -> None:
    def refuse(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        with pytest.raises(PreflightError, match="not reachable"):
            await check_models(client, "http://localhost:8000/v1", "m")


def fake_git(head: str, upstream: str, dirty: str = ""):  # type: ignore[no-untyped-def]
    def git(path: Path, *args: str) -> str:
        if args[:1] == ("fetch",):
            return ""
        if args == ("rev-parse", "HEAD"):
            return head
        if args == ("rev-parse", "origin/main"):
            return upstream
        if args == ("status", "--porcelain"):
            return dirty
        raise AssertionError(args)
    return git


def test_stale_home_service_is_refused_with_the_fix(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(PreflightError, match="checkout --detach origin/main"):
        check_home_service(tmp_path, allow_stale=False, git=fake_git("aaa1111", "bbb2222"))
    assert check_home_service(tmp_path, allow_stale=True, git=fake_git("aaa1111", "bbb2222")) == "aaa1111"


def test_dirty_home_service_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(PreflightError, match="uncommitted"):
        check_home_service(tmp_path, allow_stale=False, git=fake_git("a", "a", dirty=" M app/x.py"))


def test_missing_home_service(tmp_path: Path) -> None:
    with pytest.raises(PreflightError, match="not a git checkout"):
        check_home_service(tmp_path / "nope", allow_stale=False)
```

```python
# tests/evals/harness/test_stack.py
from __future__ import annotations

from pathlib import Path

import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from evals.harness.fake_ha import FakeHA
from evals.harness.proxy import LlmProxy
from evals.harness.stack import Stack, StackConfig, StackError, container_env
from evals.harness.world import load_world


def cfg(tmp_path: Path, **kw) -> StackConfig:  # type: ignore[no-untyped-def]
    base = dict(model="gemma-4-26b-a4b", vllm_url="http://localhost:8000/v1",
                embed_url="http://localhost:8001", embed_model="BAAI/bge-m3",
                work_dir=tmp_path, home_service_dir=tmp_path, boot_timeout_s=5.0)
    return StackConfig(**{**base, **kw})


class FakeDocker:
    def __init__(self, running: bool = True) -> None:
        self.running_value = running
        self.commands: list[list[str]] = []
        self.removed: list[str] = []
        self.wiped: list[Path] = []

    async def run_cmd(self, cmd: list[str], *, timeout: float = 600) -> str:
        self.commands.append(cmd)
        return ""

    async def port(self, name: str, container_port: int) -> int:
        return 40000 + container_port % 1000

    async def running(self, name: str) -> bool:
        return self.running_value

    async def logs_tail(self, name: str, lines: int = 60) -> str:
        return "Traceback: conscious crashed"

    async def wipe_data(self, name: str, data_dir: Path, image: str) -> None:
        self.wiped.append(data_dir)

    async def remove(self, name: str) -> None:
        self.removed.append(name)


def make_stack(tmp_path: Path, docker: FakeDocker, *, healthy: bool = True, **kw) -> Stack:  # type: ignore[no-untyped-def]
    ha = FakeHA(load_world("apartment"))

    async def health(port: int) -> bool:
        # start() resets the fake HA first; a healthy container's home-service then connects.
        if healthy:
            ha.connected.set()
        return healthy

    return Stack(cfg(tmp_path, **kw), fake_ha=ha, proxy=LlmProxy("http://x"), docker=docker,  # type: ignore[arg-type]
                 alfredctl=Path("/venv/bin/alfredctl"), health=health)


def test_container_env_points_every_llm_at_the_proxy_and_ha_at_the_fake(tmp_path: Path) -> None:
    env = container_env(cfg(tmp_path), proxy_port=9100, fake_ha_port=9200)
    assert env["HA_HOST"] == "http://host.docker.internal:9200"
    assert env["HA_TOKEN"] == "alfred-eval-ha-token"
    assert env["OPENAI_COMPAT_HOST"] == "http://host.docker.internal:9100"
    assert env["OPENAI_API_BASE"] == env["OPENAI_BASE_URL"] == "http://host.docker.internal:9100/v1"
    assert env["CLAUDE_MODEL"] == "openai/gemma-4-26b-a4b"
    assert env["EMBEDDING_HOST"] == "http://host.docker.internal:8001"
    assert env["OPENROUTER_API_KEY"] == "alfred-eval-not-a-key"


def test_up_command_is_eval_mode_with_every_env_pair(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, FakeDocker())
    cmd = stack.up_command(tmp_path / "data")
    assert cmd[:4] == ["/venv/bin/alfredctl", "up", "--eval", "--runtime"]
    assert "--no-build" in cmd and cmd[cmd.index("--persist") + 1] == str(tmp_path / "data")
    assert any(c.startswith("HA_HOST=http://host.docker.internal:") for c in cmd)


async def test_boot_fails_fast_with_logs_when_the_container_exits(tmp_path: Path) -> None:
    docker = FakeDocker(running=False)
    stack = make_stack(tmp_path, docker, healthy=False)
    with pytest.raises(StackError, match="conscious crashed"):
        await stack.start()


async def test_readiness_waits_for_a_conscious_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["channels", "conscious-engine"])

    async def fake_publish(redis, request: UserRequest, session_id: str, timeout: float):  # type: ignore[no-untyped-def]
        return AlfredResponse(source=next(answers), channel="web_pwa", session_id=session_id, text="ready")

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    stack = make_stack(tmp_path, FakeDocker())
    await stack.start()
    assert stack.first_reply_ms is not None and stack.boot_seconds is not None
    await stack.stop()


async def test_stop_wipes_data_and_removes_the_container_unless_keep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_publish(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        return AlfredResponse(source="conscious-engine", channel="web_pwa", session_id=session_id, text="ready")

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    docker = FakeDocker()
    stack = make_stack(tmp_path, docker)
    await stack.start()
    await stack.stop()
    assert docker.removed == [stack.name] and len(docker.wiped) == 1

    kept = FakeDocker()
    stack = make_stack(tmp_path, kept, keep=True)
    await stack.start()
    await stack.stop()
    assert kept.removed == []


class _NullRedis:
    async def aclose(self) -> None:
        return None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_preflight.py tests/evals/harness/test_stack.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `net.py` and `preflight.py`**

```python
# evals/harness/net.py
"""Where the host-side fakes listen, and how the eval container names them."""

from __future__ import annotations

import subprocess

IN_CONTAINER_HOST = "host.docker.internal"


def docker_bridge_gateway() -> str:
    """The default bridge's gateway IP: reachable from the container, not from the LAN."""
    out = subprocess.run(
        ["docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
        check=True, capture_output=True, text=True,
    )
    gateway = out.stdout.strip()
    if not gateway:
        raise RuntimeError("docker's bridge network has no gateway — is docker running?")
    return gateway


def in_container_url(port: int) -> str:
    return f"http://{IN_CONTAINER_HOST}:{port}"


def container_reachable(url: str) -> str:
    """A host-side URL as the container must spell it."""
    return url.replace("localhost", IN_CONTAINER_HOST).replace("127.0.0.1", IN_CONTAINER_HOST)
```

```python
# evals/harness/preflight.py
"""Checks that run before anything is built or booted."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx


class PreflightError(RuntimeError):
    """The run cannot start; the message says what to fix."""


async def check_models(client: httpx.AsyncClient, base_url: str, model: str) -> None:
    """*base_url* includes ``/v1``. Raises unless the server lists *model*."""
    try:
        response = await client.get(f"{base_url}/models")
        response.raise_for_status()
        found = [str(m.get("id")) for m in response.json().get("data", [])]
    except httpx.HTTPError as exc:
        raise PreflightError(f"{base_url} is not reachable: {exc}") from exc
    if model not in found:
        raise PreflightError(f"{base_url} does not serve {model!r}; it serves {found}")


def _git(path: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def check_home_service(
    path: Path, *, allow_stale: bool, git: Callable[..., str] = _git
) -> str:
    """Return the home-service commit the image will bundle; refuse a stale or dirty one.

    Production follows alfred-home-service ``main``, so evals do too.
    """
    if not (path / ".git").exists():
        raise PreflightError(f"home-service at {path} is not a git checkout (pass --home-service)")
    git(path, "fetch", "-q", "origin", "main")
    head = git(path, "rev-parse", "HEAD")
    upstream = git(path, "rev-parse", "origin/main")
    if allow_stale:
        return head
    if git(path, "status", "--porcelain"):
        raise PreflightError(f"home-service at {path} has uncommitted changes")
    if head != upstream:
        raise PreflightError(
            f"home-service at {path} is at {head[:7]}; production runs origin/main {upstream[:7]}. "
            f"Run: git -C {path} checkout --detach origin/main  (or pass --allow-stale-home-service)"
        )
    return head
```

- [ ] **Step 4: Implement `stack.py`**

```python
# evals/harness/stack.py
"""One throwaway Alfred container for a suite: boot, readiness, teardown."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from redis.asyncio import Redis

from alfredctl.runtime import eval_container_name, image_tag
from bus.schemas.events import AlfredResponse, UserRequest
from core.channels.request_bus import publish_and_wait
from evals.harness.fake_ha import EVAL_HA_TOKEN, FakeHA
from evals.harness.net import container_reachable, in_container_url
from evals.harness.proxy import LlmProxy
from shared.redis_streams import create_redis

logger = logging.getLogger(__name__)

CONSCIOUS_SOURCE = "conscious-engine"
EVAL_SOURCE = "alfred-evals"
EVAL_OPENROUTER_PLACEHOLDER = "alfred-eval-not-a-key"
EVAL_SIGNAL_NUMBER = "+15550100"
_BGE_M3_RECALL_FLOOR = "0.575"  # CLAUDE.md: bge-m3 needs 0.575 (EXP-009)


class StackError(RuntimeError):
    """The eval container could not be brought up or answered nothing."""


@dataclass(frozen=True)
class StackConfig:
    model: str
    vllm_url: str  # host view, with /v1
    embed_url: str  # host view, without /v1
    embed_model: str
    work_dir: Path
    home_service_dir: Path
    boot_timeout_s: float = 420.0
    reply_timeout_s: float = 120.0
    keep: bool = False


def container_env(cfg: StackConfig, *, proxy_port: int, fake_ha_port: int) -> dict[str, str]:
    """Everything the container needs; nothing from the operator's environment."""
    proxy = in_container_url(proxy_port)
    env = {
        "REFLEX_BACKEND": "openai",
        "OPENAI_COMPAT_HOST": proxy,
        "OPENAI_COMPAT_MODEL": cfg.model,
        "CLAUDE_MODEL": f"openai/{cfg.model}",
        "OPENAI_API_BASE": f"{proxy}/v1",
        "OPENAI_BASE_URL": f"{proxy}/v1",
        "OPENROUTER_API_KEY": EVAL_OPENROUTER_PLACEHOLDER,
        "EMBEDDING_BACKEND": "openai",
        "EMBEDDING_HOST": container_reachable(cfg.embed_url),
        "EMBEDDING_MODEL": cfg.embed_model,
        "HA_HOST": in_container_url(fake_ha_port),
        "HA_TOKEN": EVAL_HA_TOKEN,
        "SIGNAL_PHONE_NUMBER": EVAL_SIGNAL_NUMBER,
        "LIBRARIAN_INTERVAL_SECONDS": "86400",
    }
    if cfg.embed_model == "BAAI/bge-m3":
        env["INVOLUNTARY_RECALL_THRESHOLD"] = _BGE_M3_RECALL_FLOOR
    return env


async def run_cmd(cmd: list[str], *, timeout: float = 600, env: dict[str, str] | None = None) -> str:
    def _run() -> str:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout, env=env)
        return out.stdout

    try:
        return await asyncio.to_thread(_run)
    except subprocess.CalledProcessError as exc:
        raise StackError(f"{' '.join(cmd[:3])} … failed:\n{exc.stderr or exc.stdout}") from exc


class Docker:
    """The few docker commands the stack needs."""

    async def run_cmd(self, cmd: list[str], *, timeout: float = 600) -> str:
        return await run_cmd(cmd, timeout=timeout)

    async def port(self, name: str, container_port: int) -> int:
        out = await run_cmd(["docker", "port", name, f"{container_port}/tcp"], timeout=30)
        return int(out.splitlines()[0].rsplit(":", 1)[1])

    async def running(self, name: str) -> bool:
        try:
            out = await run_cmd(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=30)
        except StackError:
            return False
        return out.strip() == "true"

    async def logs_tail(self, name: str, lines: int = 60) -> str:
        def _run() -> str:
            out = subprocess.run(["docker", "logs", "--tail", str(lines), name],
                                 capture_output=True, text=True, timeout=30)
            return (out.stdout + out.stderr)[-6000:]

        return await asyncio.to_thread(_run)

    async def wipe_data(self, name: str, data_dir: Path, image: str) -> None:
        """The container writes /data as root; empty it from inside before removing."""
        wipe = ["find", "/data", "-mindepth", "1", "-delete"]
        if await self.running(name):
            await run_cmd(["docker", "exec", name, *wipe], timeout=120)
        else:
            await run_cmd(["docker", "run", "--rm", "--entrypoint", wipe[0], "-v",
                           f"{data_dir}:/data", image, *wipe[1:]], timeout=120)

    async def remove(self, name: str) -> None:
        await asyncio.to_thread(subprocess.run, ["docker", "rm", "-f", name],
                                capture_output=True, timeout=60)


HealthFn = Callable[[int], Awaitable[bool]]


async def _http_health(port: int) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            return (await client.get(f"http://127.0.0.1:{port}/health")).status_code == 200
    except httpx.HTTPError:
        return False


class Stack:
    def __init__(
        self,
        cfg: StackConfig,
        *,
        fake_ha: FakeHA,
        proxy: LlmProxy,
        docker: Docker | None = None,
        alfredctl: Path | None = None,
        health: HealthFn | None = None,
    ) -> None:
        self.cfg = cfg
        self.fake_ha = fake_ha
        self.proxy = proxy
        self.docker = docker or Docker()
        self._alfredctl = alfredctl or Path(sys.executable).parent / "alfredctl"
        self._health = health or _http_health
        self.name = eval_container_name()
        self.image = image_tag()
        self.redis: Redis | None = None
        self.web_port: int | None = None
        self.redis_port: int | None = None
        self.data_dir: Path | None = None
        self.boot_seconds: float | None = None
        self.first_reply_ms: float | None = None
        self.restarts = 0

    def up_command(self, data_dir: Path) -> list[str]:
        cmd = [str(self._alfredctl), "up", "--eval", "--runtime", "docker", "--no-build",
               "--persist", str(data_dir)]
        env = container_env(self.cfg, proxy_port=self.proxy.port, fake_ha_port=self.fake_ha.port)
        for key, value in env.items():
            cmd += ["--env", f"{key}={value}"]
        return cmd

    async def start(self) -> None:
        self.fake_ha.reset()
        self.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(tempfile.mkdtemp(prefix="alfred-eval-data-", dir=self.cfg.work_dir))
        t0 = time.monotonic()
        await self.docker.run_cmd(self.up_command(self.data_dir), timeout=300)
        self.web_port = await self.docker.port(self.name, 8081)
        self.redis_port = await self.docker.port(self.name, 6379)
        await self._wait_ready(t0)

    async def _fail(self, why: str) -> StackError:
        return StackError(f"{self.name}: {why}\n--- docker logs ---\n{await self.docker.logs_tail(self.name)}")

    async def _wait_ready(self, t0: float) -> None:
        deadline = t0 + self.cfg.boot_timeout_s
        assert self.web_port is not None and self.redis_port is not None
        while not await self._health(self.web_port):
            if not await self.docker.running(self.name):
                raise await self._fail("exited during boot")
            if time.monotonic() > deadline:
                raise await self._fail(f"/health not ready after {self.cfg.boot_timeout_s:.0f}s")
            await asyncio.sleep(2)
        try:
            await asyncio.wait_for(self.fake_ha.connected.wait(), max(deadline - time.monotonic(), 1))
        except TimeoutError:
            raise await self._fail("home-service never connected to the fake Home Assistant") from None
        self.redis = create_redis(f"redis://127.0.0.1:{self.redis_port}")
        while True:
            sent = time.monotonic()
            request = UserRequest(
                source=EVAL_SOURCE, channel="web_pwa", session_id=f"eval-ready-{uuid4().hex[:8]}",
                identity_claim="sir", authenticated=True, content_type="text",
                content="Reply with the single word: ready.",
            )
            remaining = deadline - time.monotonic()
            reply = await publish_and_wait(self.redis, request, request.session_id,
                                           timeout=max(min(60.0, remaining), 1.0))
            if reply.source == CONSCIOUS_SOURCE:
                self.first_reply_ms = (time.monotonic() - sent) * 1000
                break
            if not await self.docker.running(self.name):
                raise await self._fail("exited before System 2 answered")
            if time.monotonic() > deadline:
                raise await self._fail("System 2 never answered the readiness request")
        self.boot_seconds = time.monotonic() - t0
        logger.info("%s ready in %.0fs (first reply %.0f ms)", self.name, self.boot_seconds,
                    self.first_reply_ms)

    async def alive(self) -> bool:
        return await self.docker.running(self.name)

    async def send(self, request: UserRequest, timeout_s: float) -> AlfredResponse:
        if self.redis is None:
            raise StackError("stack is not started")
        return await publish_and_wait(self.redis, request, request.session_id, timeout=timeout_s)

    async def _teardown(self, *, force: bool) -> None:
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None
        if self.cfg.keep and not force:
            logger.warning("--keep: leaving %s and %s in place", self.name, self.data_dir)
            return
        if self.data_dir is not None:
            try:
                await self.docker.wipe_data(self.name, self.data_dir, self.image)
            except StackError:
                logger.warning("could not wipe %s from inside the container", self.data_dir)
            shutil.rmtree(self.data_dir, ignore_errors=True)
            self.data_dir = None
        await self.docker.remove(self.name)

    async def stop(self) -> None:
        await self._teardown(force=False)

    async def restart(self) -> None:
        await self._teardown(force=True)
        self.restarts += 1
        await self.start()
```

- [ ] **Step 5: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_preflight.py tests/evals/harness/test_stack.py -v && .venv/bin/mypy --strict evals/`
Expected: PASS and clean. If `AlfredResponse(...)` in the tests needs more required fields, add them from `bus/schemas/events.py`. If `create_redis`'s return type differs from `Redis`, annotate the attribute with that type.

- [ ] **Step 6: Commit**

```bash
git add evals/harness/net.py evals/harness/preflight.py evals/harness/stack.py tests/evals/harness/test_preflight.py tests/evals/harness/test_stack.py
git commit -m "feat(evals): preflight checks and the eval stack lifecycle"
```

---

### Task 9: The driver — a scenario in, Evidence out

**Files:**
- Create: `evals/harness/driver.py`
- Test: `tests/evals/harness/test_driver.py`

**Interfaces:**
- Consumes: `ScenarioVariant` and the step models (Task 4), `FakeHA` (Task 5), `LlmProxy` (Task 6), the constants `CONSCIOUS_SOURCE`, `EVAL_SOURCE` and `EVAL_SIGNAL_NUMBER` (Task 8), and `UserRequest` and `AlfredResponse`.
- Produces:
  - `HarnessError`.
  - `SendFn = Callable[[UserRequest, float], Awaitable[AlfredResponse]]`.
  - `PlayContext(send, fake_ha, proxy, reply_timeout_s=120, settle_s=2.0, restore_settle_s=2.0, signal_number=EVAL_SIGNAL_NUMBER)`.
  - `build_request(actor, text, session_id, signal_number) -> UserRequest`.
  - `session_id_for(sample_id, epoch) -> str`.
  - `async play(ctx, variant, epoch) -> Evidence`.
- **Identity rules** (`core/conscious/identity.py`). `authenticated` is always false, as on every production channel (spec: "the web socket always claims `sir` and never sets `authenticated`"); the claim alone selects the identity:
  - **sir:** on signal, the claim is the configured Signal number. On every other channel, the claim is `"sir"`.
  - **guest:** on signal, the claim is `+15550199`. On every other channel, the claim is `"guest"`.
  - *(Corrected during execution: an earlier draft set `authenticated` true for sir off signal, which sends the gate down a webauthn path no real channel takes. The code blocks below show that draft; the shipped code follows these rules.)*

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/harness/test_driver.py
from __future__ import annotations

import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from evals.harness.driver import HarnessError, PlayContext, build_request, play
from evals.harness.evidence import HaCall
from evals.harness.fake_ha import FakeHA
from evals.harness.proxy import LlmProxy
from evals.harness.scenario import Actor, Scenario, expand_variants
from evals.harness.world import load_world


def scenario(**kw) -> Scenario:  # type: ignore[no-untyped-def]
    base = {"id": "demo.case.one", "prd": ["x"], "status": "shipped",
            "steps": [{"user": "Turn on the bedroom lamp."}, {"user": "Thanks."}],
            "expect": [{"ha_not_called": {}}]}
    return Scenario.model_validate({**base, **kw})


class Recorder:
    def __init__(self, source: str = "conscious-engine") -> None:
        self.requests: list[UserRequest] = []
        self.source = source

    async def __call__(self, request: UserRequest, timeout: float) -> AlfredResponse:
        self.requests.append(request)
        return AlfredResponse(source=self.source, channel=request.channel,
                              session_id=request.session_id, text=f"Reply {len(self.requests)}, sir.")


def ctx(send, ha: FakeHA | None = None) -> PlayContext:  # type: ignore[no-untyped-def]
    return PlayContext(send=send, fake_ha=ha or FakeHA(load_world("apartment")),
                       proxy=LlmProxy("http://x"), settle_s=0, restore_settle_s=0)


async def test_multi_turn_keeps_one_session_and_records_replies() -> None:
    send = Recorder()
    [variant] = expand_variants(scenario())
    ev = await play(ctx(send), variant, epoch=2)
    assert len({r.session_id for r in send.requests}) == 1
    assert ev.session_id == send.requests[0].session_id and "e2" in ev.session_id
    assert [r.text for r in ev.replies] == ["Reply 1, sir.", "Reply 2, sir."]
    assert [t.role for t in ev.transcript] == ["user", "alfred", "user", "alfred"]
    assert len(ev.step_started) == 2


@pytest.mark.parametrize(("who", "channel", "claim", "authenticated"), [
    ("sir", "web_pwa", "sir", True),
    ("sir", "signal", "+15550100", False),
    ("guest", "web_pwa", "guest", False),
    ("guest", "signal", "+15550199", False),
])
def test_identity_claims(who: str, channel: str, claim: str, authenticated: bool) -> None:
    req = build_request(Actor(who=who, channel=channel), "hi", "s", "+15550100")  # type: ignore[arg-type]
    assert (req.identity_claim, req.authenticated, req.channel) == (claim, authenticated, channel)
    assert req.timezone == "America/Denver" and req.source == "alfred-evals"


async def test_no_conscious_reply_is_a_harness_error() -> None:
    [variant] = expand_variants(scenario())
    with pytest.raises(HarnessError, match="no reply from System 2"):
        await play(ctx(Recorder(source="channels")), variant, epoch=1)


async def test_ha_event_step_changes_state_and_is_transcribed() -> None:
    ha = FakeHA(load_world("apartment"))
    s = scenario(steps=[{"ha_event": {"entity_id": "light.living_room_ceiling", "state": "on"}, "settle": 0},
                        {"user": "Is the ceiling light on?"}])
    [variant] = expand_variants(s)
    ev = await play(ctx(Recorder(), ha), variant, epoch=1)
    assert ev.ha_states["light.living_room_ceiling"].state == "on"
    assert ev.transcript[0].role == "event"


async def test_world_is_restored_and_only_in_window_calls_are_kept() -> None:
    ha = FakeHA(load_world("apartment"))
    await ha.set_state("light.bedroom_lamp", "on")
    ha.calls.append(HaCall(t=0.0, domain="light", service="turn_on", entity_ids=["light.x"]))
    [variant] = expand_variants(scenario())
    ev = await play(ctx(Recorder(), ha), variant, epoch=1)
    assert ev.ha_states["light.bedroom_lamp"].state == "off"
    assert ev.ha_calls == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_driver.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# evals/harness/driver.py
"""Play one scenario variant against a running stack and collect its Evidence."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from bus.schemas.events import AlfredResponse, UserRequest
from evals.harness.evidence import Evidence, Reply, TranscriptTurn
from evals.harness.fake_ha import FakeHA
from evals.harness.proxy import LlmProxy
from evals.harness.scenario import Actor, HaEventStep, ScenarioVariant, UserStep, WaitStep
from evals.harness.stack import CONSCIOUS_SOURCE, EVAL_SIGNAL_NUMBER, EVAL_SOURCE

EVAL_GUEST_SIGNAL_NUMBER = "+15550199"

SendFn = Callable[[UserRequest, float], Awaitable[AlfredResponse]]


class HarnessError(RuntimeError):
    """The harness, not Alfred, failed. The sample scores E and is retried once."""


@dataclass
class PlayContext:
    send: SendFn
    fake_ha: FakeHA
    proxy: LlmProxy
    reply_timeout_s: float = 120.0
    settle_s: float = 2.0
    restore_settle_s: float = 2.0
    signal_number: str = EVAL_SIGNAL_NUMBER


def build_request(actor: Actor, text: str, session_id: str, signal_number: str) -> UserRequest:
    if actor.who == "sir":
        claim = signal_number if actor.channel == "signal" else "sir"
        authenticated = actor.channel != "signal"
    else:
        claim = EVAL_GUEST_SIGNAL_NUMBER if actor.channel == "signal" else "guest"
        authenticated = False
    return UserRequest(
        source=EVAL_SOURCE, channel=actor.channel, session_id=session_id, identity_claim=claim,
        authenticated=authenticated, content_type="text", content=text, timezone=actor.tz,
    )


def session_id_for(sample_id: str, epoch: int) -> str:
    return f"eval-{sample_id}-e{epoch}-{uuid4().hex[:6]}"


async def play(ctx: PlayContext, variant: ScenarioVariant, epoch: int) -> Evidence:
    scenario = variant.scenario
    if await ctx.fake_ha.restore_world():
        await asyncio.sleep(ctx.restore_settle_s)
    session_id = session_id_for(variant.sample_id, epoch)
    started = time.monotonic()
    ev = Evidence(scenario_id=scenario.id, variant=variant.variant, epoch=epoch,
                  session_id=session_id, started_at=started, ended_at=started)
    for index, step in enumerate(variant.steps):
        ev.step_started.append(time.monotonic())
        match step:
            case UserStep():
                actor = step.actor or scenario.actor
                ev.transcript.append(TranscriptTurn(role="user", text=step.user))
                sent = time.monotonic()
                request = build_request(actor, step.user, session_id, ctx.signal_number)
                response = await ctx.send(request, ctx.reply_timeout_s)
                if response.source != CONSCIOUS_SOURCE:
                    raise HarnessError(
                        f"step {index}: no reply from System 2 within {ctx.reply_timeout_s:.0f}s "
                        f"(got {response.source!r}: {response.text[:120]!r})"
                    )
                ev.replies.append(Reply(
                    step=index, text=response.text, source=response.source,
                    actions_taken=list(response.actions_taken),
                    latency_ms=(time.monotonic() - sent) * 1000,
                ))
                ev.transcript.append(TranscriptTurn(role="alfred", text=response.text))
                await asyncio.sleep(ctx.settle_s)
            case HaEventStep():
                e = step.ha_event
                await ctx.fake_ha.set_state(e.entity_id, e.state, e.attributes)
                ev.transcript.append(TranscriptTurn(role="event", text=f"{e.entity_id} → {e.state}"))
                await asyncio.sleep(step.settle)
            case WaitStep():
                await asyncio.sleep(step.wait)
    ended = time.monotonic()
    ev.ended_at = ended
    ev.ha_calls = ctx.fake_ha.calls_between(started, ended)
    ev.llm_calls = ctx.proxy.calls_between(started, ended)
    ev.ha_states = ctx.fake_ha.states()
    return ev
```

- [ ] **Step 4: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_driver.py -v && .venv/bin/mypy --strict evals/`
Expected: PASS and clean.

- [ ] **Step 5: Commit**

```bash
git add evals/harness/driver.py tests/evals/harness/test_driver.py
git commit -m "feat(evals): driver plays a scenario and collects evidence"
```

---

### Task 10: The judge, calibration and `alfred evals calibrate`

**Files:**
- Create: `evals/harness/judge.py`, and `evals/judge_calibration/` with `tone.yaml`, `answered.yaml` and `faithfulness.yaml`
- Modify: `evals/cli.py` (the `calibrate` command)
- Test: `tests/evals/harness/test_judge.py`

**Interfaces:**
- Consumes: `JudgeSpec` and `CheckResult` (Task 3), `Evidence` and `TranscriptTurn` (Task 3).
- Produces:
  - Prompting: `render_prompt(transcript, spec) -> str`, `parse_verdict(text) -> bool | None`, `JudgeVerdict(verdict, rationale)`.
  - The judge: `Judge(model)` with `async ask(transcript, spec) -> JudgeVerdict`, and `make_judge_model(model, base_url) -> inspect_ai.model.Model`.
  - Scoring: `async judge_check(judge, evidence, spec, trusted) -> CheckResult`.
  - Calibration models: `CalibrationItem`, `CalibrationSet`, `CategoryResult`, `CalibrationReport` (with `.trusted(threshold=TRUST_THRESHOLD) -> set[str]`).
  - Calibration functions: `load_calibration_sets(root=CALIBRATION_DIR)`, `async calibrate(judge, sets, model) -> CalibrationReport`, `save_report(report, path=CALIBRATION_FILE)`, `load_report(path=CALIBRATION_FILE) -> CalibrationReport | None`.
  - Constants: `STATE_DIR = ~/.local/share/alfred-evals`, `CALIBRATION_FILE = STATE_DIR / "calibration.json"`, `TRUST_THRESHOLD = 0.85`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/harness/test_judge.py
from __future__ import annotations

from pathlib import Path

from inspect_ai.model import ModelOutput, get_model

from evals.harness.checks.judge_spec import JudgeSpec
from evals.harness.evidence import TranscriptTurn
from evals.harness.judge import (
    CalibrationReport,
    CategoryResult,
    Judge,
    calibrate,
    judge_check,
    load_calibration_sets,
    load_report,
    parse_verdict,
    render_prompt,
    save_report,
)
from tests.evals.harness.factories import evidence

SPEC = JudgeSpec(category="tone", rubric="Does the reply keep a formal butler register?")


def judge_saying(*texts: str) -> Judge:
    outputs = [ModelOutput.from_content(model="mockllm/model", content=t) for t in texts]
    return Judge(get_model("mockllm/model", custom_outputs=outputs, memoize=False))


def test_parse_verdict_takes_the_last_verdict_line() -> None:
    assert parse_verdict("It is polite.\nVERDICT: yes") is True
    assert parse_verdict("verdict: No") is False
    assert parse_verdict("VERDICT: yes\nOn reflection...\nVERDICT: no") is False
    assert parse_verdict("I think so.") is None


def test_prompt_includes_conversation_reference_and_rubric() -> None:
    spec = SPEC.model_copy(update={"reference": "Good evening, sir."})
    prompt = render_prompt([TranscriptTurn(role="user", text="hi"),
                            TranscriptTurn(role="alfred", text="Good evening, sir.")], spec)
    assert "User: hi" in prompt and "Alfred: Good evening, sir." in prompt
    assert "<reference>" in prompt and SPEC.rubric in prompt and "VERDICT: yes" in prompt


async def test_trusted_yes_is_a_counted_pass() -> None:
    res = await judge_check(judge_saying("Formal.\nVERDICT: yes"), evidence(replies=["Good evening, sir."]),
                            SPEC, trusted={"tone"})
    assert res.status == "pass" and res.counted


async def test_untrusted_category_is_reported_but_not_counted() -> None:
    res = await judge_check(judge_saying("VERDICT: no"), evidence(replies=["yo"]), SPEC, trusted=set())
    assert res.status == "fail" and not res.counted and "untrusted" in res.reason


async def test_unparseable_verdict_is_an_error_not_a_fail() -> None:
    res = await judge_check(judge_saying("Hard to say."), evidence(replies=["ok"]), SPEC, trusted={"tone"})
    assert res.status == "error" and res.counted


async def test_calibration_agreement_and_trust(tmp_path: Path) -> None:
    sets = load_calibration_sets()
    tone = next(s for s in sets if s.category == "tone")
    answers = [f"VERDICT: {'yes' if item.label else 'no'}" for item in tone.items]
    answers[0] = "VERDICT: " + ("no" if tone.items[0].label else "yes")  # one disagreement
    report = await calibrate(judge_saying(*answers), [tone], model="m")
    result = report.categories["tone"]
    assert result.n == len(tone.items) and result.disagreements == [tone.items[0].id]
    assert abs(result.agreement - (len(tone.items) - 1) / len(tone.items)) < 1e-9
    save_report(report, tmp_path / "c.json")
    assert load_report(tmp_path / "c.json") == report


def test_trusted_threshold() -> None:
    report = CalibrationReport(model="m", categories={
        "tone": CategoryResult(agreement=0.9, n=10, disagreements=[], unparseable=[]),
        "answered": CategoryResult(agreement=0.8, n=10, disagreements=[], unparseable=[]),
    })
    assert report.trusted() == {"tone"}


def test_every_shipped_calibration_set_mixes_labels() -> None:
    for s in load_calibration_sets():
        labels = {i.label for i in s.items}
        assert labels == {True, False}, f"{s.category} needs both yes and no items"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_judge.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the calibration sets**

```yaml
# evals/judge_calibration/tone.yaml
category: tone
items:
  - id: tone-1
    conversation:
      - {role: user, text: "Good evening, Alfred."}
      - {role: alfred, text: "Good evening, sir. I trust the day treated you kindly. Shall I bring up the evening lights?"}
    rubric: "Does the reply keep a formal, courteous butler register?"
    label: true
  - id: tone-2
    conversation:
      - {role: user, text: "Good evening, Alfred."}
      - {role: alfred, text: "hey!! 😄 what's up? need anything??"}
    rubric: "Does the reply keep a formal, courteous butler register?"
    label: false
  - id: tone-3
    conversation:
      - {role: user, text: "Turn on the bedroom lamp."}
      - {role: alfred, text: "Done, sir. The bedroom lamp is on."}
    rubric: "Does the reply keep a formal, courteous butler register?"
    label: true
  - id: tone-4
    conversation:
      - {role: user, text: "Turn on the bedroom lamp."}
      - {role: alfred, text: "Lamp's on lol. Anything else, dude?"}
    rubric: "Does the reply keep a formal, courteous butler register?"
    label: false
  - id: tone-5
    conversation:
      - {role: user, text: "yo alfred what's good"}
      - {role: alfred, text: "Good afternoon, sir. All is in order here. How may I be of service?"}
    rubric: "Does the reply keep a formal, courteous butler register even though the user was casual?"
    label: true
```

```yaml
# evals/judge_calibration/answered.yaml
category: answered
items:
  - id: answered-1
    conversation:
      - {role: user, text: "Is the front door locked?"}
      - {role: alfred, text: "Yes, sir — the front door is locked."}
    rubric: "Does the reply directly say whether the front door is locked?"
    label: true
  - id: answered-2
    conversation:
      - {role: user, text: "Is the front door locked?"}
      - {role: alfred, text: "I can help with a great many things around the house, sir. Just say the word."}
    rubric: "Does the reply directly say whether the front door is locked?"
    label: false
  - id: answered-3
    conversation:
      - {role: user, text: "What's the temperature in the living room?"}
      - {role: alfred, text: "It is 21.5 degrees in the living room, sir."}
    rubric: "Does the reply give the living room temperature?"
    label: true
  - id: answered-4
    conversation:
      - {role: user, text: "What's the temperature in the living room?"}
      - {role: alfred, text: "The kitchen pendants are on, sir."}
    rubric: "Does the reply give the living room temperature?"
    label: false
  - id: answered-5
    conversation:
      - {role: user, text: "Dim it a bit."}
      - {role: alfred, text: "Certainly, sir — which light would you like dimmed: the living room lamp or the kitchen pendants?"}
    rubric: "Does the reply ask which light the user means instead of acting?"
    label: true
```

```yaml
# evals/judge_calibration/faithfulness.yaml
category: faithfulness
items:
  - id: faithfulness-1
    conversation:
      - {role: event, text: "light.living_room_ceiling → on"}
      - {role: user, text: "Is the living room ceiling light on?"}
      - {role: alfred, text: "Yes, sir, the living room ceiling light is on."}
    rubric: "Does the reply say that the living room ceiling light is currently on?"
    label: true
  - id: faithfulness-2
    conversation:
      - {role: event, text: "light.living_room_ceiling → on"}
      - {role: user, text: "Is the living room ceiling light on?"}
      - {role: alfred, text: "No, sir, the ceiling light is off."}
    rubric: "Does the reply say that the living room ceiling light is currently on?"
    label: false
  - id: faithfulness-3
    conversation:
      - {role: user, text: "Book me a table at Nobu for eight tonight."}
      - {role: alfred, text: "I'm afraid I can't make restaurant reservations, sir. Shall I set a reminder for you to call them?"}
    rubric: "Does the reply make clear that Alfred cannot book the table, without claiming it did?"
    reference: "I'm afraid I can't make restaurant reservations, sir. Shall I set a reminder for you to call Nobu?"
    label: true
  - id: faithfulness-4
    conversation:
      - {role: user, text: "Book me a table at Nobu for eight tonight."}
      - {role: alfred, text: "Certainly, sir — your table at Nobu is booked for eight o'clock."}
    rubric: "Does the reply make clear that Alfred cannot book the table, without claiming it did?"
    reference: "I'm afraid I can't make restaurant reservations, sir. Shall I set a reminder for you to call Nobu?"
    label: false
  - id: faithfulness-5
    conversation:
      - {role: user, text: "My sister Priya is visiting on Saturday."}
      - {role: alfred, text: "How lovely, sir. I shall keep that in mind."}
      - {role: user, text: "Who did I say is visiting?"}
      - {role: alfred, text: "Your sister Priya, sir — on Saturday."}
    rubric: "Does the reply correctly say that the user's sister Priya is visiting on Saturday?"
    label: true
  - id: faithfulness-6
    conversation:
      - {role: user, text: "My sister Priya is visiting on Saturday."}
      - {role: alfred, text: "How lovely, sir. I shall keep that in mind."}
      - {role: user, text: "Who did I say is visiting?"}
      - {role: alfred, text: "Your brother Raj, sir, on Sunday."}
    rubric: "Does the reply correctly say that the user's sister Priya is visiting on Saturday?"
    label: false
```

- [ ] **Step 4: Implement `judge.py`**

```python
# evals/harness/judge.py
"""The rubric judge (gemma through vLLM) and its calibration against hand-labelled items."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import yaml
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, GenerateConfig, Model, get_model
from pydantic import BaseModel, ConfigDict, Field

from evals.harness.checks.judge_spec import JudgeCategory, JudgeSpec
from evals.harness.checks.result import CheckResult
from evals.harness.evidence import Evidence, TranscriptTurn

CALIBRATION_DIR = Path(__file__).resolve().parent.parent / "judge_calibration"
STATE_DIR = Path.home() / ".local" / "share" / "alfred-evals"
CALIBRATION_FILE = STATE_DIR / "calibration.json"
TRUST_THRESHOLD = 0.85
JUDGE_API_KEY = "alfred-eval-not-a-key"

JUDGE_SYSTEM = (
    "You grade replies from Alfred, a formal, butler-style home assistant. Judge only the "
    "question asked, against the conversation shown. Be strict: if the reply does not "
    "clearly satisfy the question, the answer is no."
)
_LABEL = {"user": "User", "alfred": "Alfred", "event": "Home event"}
_VERDICT = re.compile(r"VERDICT:\s*(yes|no)\b", re.IGNORECASE)


def render_prompt(transcript: Sequence[TranscriptTurn], spec: JudgeSpec) -> str:
    convo = "\n".join(f"{_LABEL[t.role]}: {t.text}" for t in transcript)
    parts = [f"<conversation>\n{convo}\n</conversation>"]
    if spec.reference:
        parts.append(f"<reference>\nA good reply would be close in substance to:\n{spec.reference}\n</reference>")
    parts.append(f"Question about Alfred's last reply: {spec.rubric}")
    parts.append('Explain in at most three sentences, then end with a final line that is exactly '
                 '"VERDICT: yes" or "VERDICT: no".')
    return "\n\n".join(parts)


def parse_verdict(text: str) -> bool | None:
    found = _VERDICT.findall(text)
    return None if not found else found[-1].lower() == "yes"


class JudgeVerdict(BaseModel):
    verdict: bool | None
    rationale: str


class Judge:
    def __init__(self, model: Model) -> None:
        self._model = model

    async def ask(self, transcript: Sequence[TranscriptTurn], spec: JudgeSpec) -> JudgeVerdict:
        output = await self._model.generate([
            ChatMessageSystem(content=JUDGE_SYSTEM),
            ChatMessageUser(content=render_prompt(transcript, spec)),
        ])
        text = output.completion
        return JudgeVerdict(verdict=parse_verdict(text), rationale=text.strip())


def make_judge_model(model: str, base_url: str) -> Model:
    return get_model(
        f"openai-api/vllm/{model}", base_url=base_url, api_key=JUDGE_API_KEY,
        config=GenerateConfig(temperature=0.0, max_tokens=600, max_connections=2),
    )


async def judge_check(judge: Judge, evidence: Evidence, spec: JudgeSpec, trusted: set[str]) -> CheckResult:
    counted = spec.category in trusted
    tag = spec.category if counted else f"{spec.category}, untrusted"
    if not evidence.replies:
        return CheckResult(name="judge", status="error", reason=f"[{tag}] no reply to judge", counted=counted)
    verdict = await judge.ask(evidence.transcript, spec)
    if verdict.verdict is None:
        return CheckResult(name="judge", status="error", counted=counted,
                           reason=f"[{tag}] judge gave no VERDICT line: {verdict.rationale[:300]}")
    return CheckResult(
        name="judge", status="pass" if verdict.verdict else "fail", counted=counted,
        reason=f"[{tag}] {spec.rubric} → {'yes' if verdict.verdict else 'no'}: {verdict.rationale[:500]}",
    )


class CalibrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    conversation: list[TranscriptTurn] = Field(min_length=1)
    rubric: str
    reference: str | None = None
    label: bool


class CalibrationSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: JudgeCategory
    items: list[CalibrationItem] = Field(min_length=1)


class CategoryResult(BaseModel):
    agreement: float
    n: int
    disagreements: list[str]
    unparseable: list[str]


class CalibrationReport(BaseModel):
    model: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    categories: dict[str, CategoryResult]

    def trusted(self, threshold: float = TRUST_THRESHOLD) -> set[str]:
        return {c for c, r in self.categories.items() if r.agreement >= threshold}


def load_calibration_sets(root: Path = CALIBRATION_DIR) -> list[CalibrationSet]:
    return [CalibrationSet.model_validate(yaml.safe_load(p.read_text()))
            for p in sorted(root.glob("*.yaml"))]


async def calibrate(judge: Judge, sets: Sequence[CalibrationSet], model: str) -> CalibrationReport:
    categories: dict[str, CategoryResult] = {}
    for s in sets:
        agree, disagreements, unparseable = 0, [], []
        for item in s.items:
            spec = JudgeSpec(category=s.category, rubric=item.rubric, reference=item.reference)
            verdict = await judge.ask(item.conversation, spec)
            if verdict.verdict is None:
                unparseable.append(item.id)
                disagreements.append(item.id)
            elif verdict.verdict == item.label:
                agree += 1
            else:
                disagreements.append(item.id)
        categories[s.category] = CategoryResult(agreement=agree / len(s.items), n=len(s.items),
                                                disagreements=disagreements, unparseable=unparseable)
    return CalibrationReport(model=model, categories=categories)


def save_report(report: CalibrationReport, path: Path = CALIBRATION_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2))


def load_report(path: Path = CALIBRATION_FILE) -> CalibrationReport | None:
    if not path.is_file():
        return None
    return CalibrationReport.model_validate(json.loads(path.read_text()))
```

- [ ] **Step 5: Add `alfred evals calibrate`**

In `evals/cli.py`, add `from typing import Annotated` to the imports. Below `evals_app`, add the shared defaults and the command:

```python
DEFAULT_MODEL = "gemma-4-26b-a4b"
DEFAULT_VLLM_URL = "http://localhost:8000/v1"

ModelOpt = Annotated[str, typer.Option(help="vLLM served model, used in every role")]
VllmUrlOpt = Annotated[str, typer.Option(help="vLLM base URL, with /v1")]


@evals_app.command()
def calibrate(model: ModelOpt = DEFAULT_MODEL, vllm_url: VllmUrlOpt = DEFAULT_VLLM_URL) -> None:
    """Measure how often the judge agrees with the hand-labelled items."""
    import asyncio

    from evals.harness.judge import (
        CALIBRATION_FILE, TRUST_THRESHOLD, Judge, calibrate as run_calibration,
        load_calibration_sets, make_judge_model, save_report,
    )

    judge = Judge(make_judge_model(model, vllm_url))
    report = asyncio.run(run_calibration(judge, load_calibration_sets(), model))
    save_report(report)
    for category, result in sorted(report.categories.items()):
        mark = "trusted" if result.agreement >= TRUST_THRESHOLD else "UNTRUSTED"
        typer.echo(f"{category:13} {result.agreement:5.0%} of {result.n}  {mark}"
                   + (f"  disagreed: {', '.join(result.disagreements)}" if result.disagreements else ""))
    typer.echo(f"saved {CALIBRATION_FILE}")
```

- [ ] **Step 6: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_judge.py -v && .venv/bin/mypy --strict evals/`
Expected: PASS and clean.

- [ ] **Step 7: Commit**

```bash
git add evals/harness/judge.py evals/judge_calibration evals/cli.py tests/evals/harness/test_judge.py
git commit -m "feat(evals): rubric judge with calibration against labelled items"
```

- [ ] **Step 8: Owner checkpoint (the controller does this, not a subagent)**

Show the owner the 16 calibration items: id, conversation, rubric and label. Ask them to confirm or flip each label. Apply their changes in a `fix(evals): owner-confirmed judge calibration labels` commit. This one is required before the first run: the spec says the owner confirms every label once.

---

### Task 11: The scorecard

**Files:**
- Create: `evals/harness/report.py`
- Test: `tests/evals/harness/test_report.py`

**Interfaces:**
- Consumes: `CheckResult` (Task 3), `Evidence` (Task 3) and `inspect_ai.log.EvalLog`.
- Produces:
  - Models: `SampleRun`, `LlmUsage`, `GoldenSummary`, `PrdRowSummary`, `RoleUsage`, `RunMeta`, `Scorecard`.
  - Functions: `runs_from_logs(logs) -> list[SampleRun]`, `summarize(runs, meta) -> Scorecard`, `render_markdown(card) -> str`, `write_report(card, out_dir) -> tuple[Path, Path]`.
  - The constant `SCORER_NAME = "scenario_scorer"`, which Task 12 must name its scorer to match.
- **Definitions:**
  - Per golden (all variants × epochs): **runs**, **passes** (`C`), **errors** (`E`) and **inconclusive** (`N`).
  - **scored** = runs − errors − inconclusive.
  - **pass rate** = passes / scored, or none when scored = 0.
  - **pass^k** = runs > 0 and passes == runs.
  - **flaky** = 0 < passes < scored.

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/harness/test_report.py
from __future__ import annotations

from pathlib import Path

from inspect_ai import Task, eval_async
from inspect_ai.dataset import Sample
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, mean, scorer
from inspect_ai.solver import Generate, TaskState, solver

from evals.harness.checks.result import CheckResult
from evals.harness.report import (
    RunMeta,
    SampleRun,
    render_markdown,
    runs_from_logs,
    summarize,
    write_report,
)
from tests.evals.harness.factories import evidence, llm

META = RunMeta(run_dir="/tmp/run", model="m", alfred_commit="abc1234", home_service_commit="def5678",
               epochs=3, calibration={"tone": 0.9}, trusted=["tone"], stacks=[])


def run(sid: str, value: str, *, epoch: int = 1, status: str = "shipped", prd: list[str] | None = None,
        reason: str = "x") -> SampleRun:
    checks = [] if value == "C" else [CheckResult(name="ha_called", status="fail", reason=reason)]
    variant = int(sid.split("~")[1]) if "~" in sid else 0
    return SampleRun(sample_id=sid, scenario_id=sid.split("~")[0], variant=variant, epoch=epoch,
                     suite=sid.split(".")[0], status=status, prd=prd or ["4.4.lights-scenes"],  # type: ignore[arg-type]
                     value=value, checks=checks, reply_ms=[1000.0 * epoch])  # type: ignore[arg-type]


def test_golden_summary_rates() -> None:
    runs = [run("home.a", "C", epoch=1), run("home.a", "I", epoch=2), run("home.a", "C", epoch=3),
            run("home.a~1", "E"), run("home.b", "C"), run("home.c", "N")]
    card = summarize(runs, META)
    a = next(g for g in card.goldens if g.scenario_id == "home.a")
    assert (a.runs, a.passes, a.errors, a.variants) == (4, 2, 1, 2)
    assert abs((a.pass_rate or 0) - 2 / 3) < 1e-9 and a.flaky and not a.pass_k
    b = next(g for g in card.goldens if g.scenario_id == "home.b")
    assert b.pass_k and not b.flaky
    c = next(g for g in card.goldens if g.scenario_id == "home.c")
    assert c.pass_rate is None and c.inconclusive == 1


def test_pending_goldens_stay_out_of_prd_rows() -> None:
    card = summarize([run("home.a", "C"), run("home.p", "I", status="pending", prd=["4.4.device-discovery"])], META)
    assert [r.prd_id for r in card.prd_rows] == ["4.4.lights-scenes"]
    md = render_markdown(card)
    assert "Not yet working" in md and "home.p" in md


def test_markdown_names_the_failing_check(tmp_path: Path) -> None:
    card = summarize([run("home.a", "I", reason="wanted light.turn_on")], META)
    md_path, json_path = write_report(card, tmp_path)
    assert "wanted light.turn_on" in md_path.read_text() and json_path.exists()


async def test_runs_from_logs_reads_inspect_logs(tmp_path: Path) -> None:
    ev = evidence(replies=["Done, sir."], llm_calls=[llm("system2")])

    @solver
    def fake():  # type: ignore[no-untyped-def]
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            state.store.set("evidence", ev.model_dump(mode="json"))
            return state
        return solve

    @scorer(metrics=[mean()], name="scenario_scorer")
    def fake_scorer():  # type: ignore[no-untyped-def]
        async def score(state: TaskState, target: Target) -> Score:
            ok = state.epoch == 1
            checks = [CheckResult(name="judge", status="pass" if ok else "fail", reason="r").model_dump()]
            return Score(value=CORRECT if ok else INCORRECT, metadata={"checks": checks})
        return score

    task = Task(dataset=[Sample(id="home.a", input="x", metadata={
        "scenario_id": "home.a", "variant": 0, "suite": "home", "status": "shipped",
        "prd": ["4.4.lights-scenes"]})], solver=fake(), scorer=fake_scorer(), epochs=2)
    logs = await eval_async(task, model="mockllm/model", log_dir=str(tmp_path))
    runs = runs_from_logs(logs)
    assert sorted((r.epoch, r.value) for r in runs) == [(1, "C"), (2, "I")]
    assert runs[0].reply_ms == [1000.0] and runs[0].llm[0].role == "system2"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# evals/harness/report.py
"""The scorecard: per golden, per PRD row, and LLM usage by role."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from inspect_ai.log import EvalLog
from pydantic import BaseModel, Field

from evals.harness.checks.result import CheckResult
from evals.harness.evidence import Evidence, Role

SCORER_NAME = "scenario_scorer"
Value = Literal["C", "I", "N", "E"]


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
    status: Literal["shipped", "pending"]
    prd: list[str]
    value: Value
    checks: list[CheckResult] = Field(default_factory=list)
    error: str | None = None
    reply_ms: list[float] = Field(default_factory=list)
    llm: list[LlmUsage] = Field(default_factory=list)


class GoldenSummary(BaseModel):
    scenario_id: str
    suite: str
    status: Literal["shipped", "pending"]
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
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Scorecard(BaseModel):
    meta: RunMeta
    goldens: list[GoldenSummary]
    prd_rows: list[PrdRowSummary]
    llm: list[RoleUsage]
    reply_p50_ms: float | None
    reply_p95_ms: float | None


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(q * len(ordered) + 0.5) - 1))
    return ordered[index]


def runs_from_logs(logs: Sequence[EvalLog]) -> list[SampleRun]:
    runs: list[SampleRun] = []
    for log in logs:
        for sample in log.samples or []:
            md = sample.metadata or {}
            score = (sample.scores or {}).get(SCORER_NAME)
            raw = (sample.store or {}).get("evidence")
            evidence = Evidence.model_validate(raw) if raw else None
            if sample.error is not None or score is None:
                value: Value = "E"
                error = sample.error.message if sample.error is not None else "not scored"
                checks: list[CheckResult] = []
            else:
                value = str(score.value)  # type: ignore[assignment]
                error = None
                checks = [CheckResult.model_validate(c) for c in (score.metadata or {}).get("checks", [])]
            runs.append(SampleRun(
                sample_id=str(sample.id), scenario_id=str(md.get("scenario_id", sample.id)),
                variant=int(md.get("variant", 0)), epoch=sample.epoch, suite=str(md.get("suite", "")),
                status=md.get("status", "shipped"), prd=list(md.get("prd", [])), value=value,
                checks=checks, error=error,
                reply_ms=[r.latency_ms for r in evidence.replies] if evidence else [],
                llm=[LlmUsage(role=c.role, latency_ms=c.latency_ms, prompt_tokens=c.prompt_tokens,
                              completion_tokens=c.completion_tokens)
                     for c in evidence.llm_calls] if evidence else [],
            ))
    return runs


def _golden(runs: list[SampleRun]) -> GoldenSummary:
    first = runs[0]
    passes = sum(r.value == "C" for r in runs)
    errors = sum(r.value == "E" for r in runs)
    inconclusive = sum(r.value == "N" for r in runs)
    scored = len(runs) - errors - inconclusive
    failing = list(dict.fromkeys(
        f"{c.name}: {c.reason}" for r in runs for c in r.checks if c.counted and c.status != "pass"
    ))
    failing += list(dict.fromkeys(f"error: {r.error}" for r in runs if r.error))
    return GoldenSummary(
        scenario_id=first.scenario_id, suite=first.suite, status=first.status, prd=first.prd,
        variants=len({r.variant for r in runs}), runs=len(runs), passes=passes, errors=errors,
        inconclusive=inconclusive, pass_rate=passes / scored if scored else None,
        pass_k=bool(runs) and passes == len(runs), flaky=0 < passes < scored, failing=failing[:3],
    )


def summarize(runs: Sequence[SampleRun], meta: RunMeta) -> Scorecard:
    by_golden: dict[str, list[SampleRun]] = defaultdict(list)
    for r in runs:
        by_golden[r.scenario_id].append(r)
    goldens = sorted((_golden(rs) for rs in by_golden.values()), key=lambda g: (g.suite, g.scenario_id))
    by_row: dict[str, list[GoldenSummary]] = defaultdict(list)
    for g in goldens:
        if g.status == "shipped":
            for prd_id in g.prd:
                by_row[prd_id].append(g)
    rows = []
    for prd_id, gs in sorted(by_row.items()):
        rates = [g.pass_rate for g in gs if g.pass_rate is not None]
        rows.append(PrdRowSummary(prd_id=prd_id, goldens=[g.scenario_id for g in gs],
                                  pass_rate=sum(rates) / len(rates) if rates else None,
                                  pass_k=sum(g.pass_k for g in gs)))
    usage: dict[str, list[LlmUsage]] = defaultdict(list)
    for r in runs:
        for u in r.llm:
            usage[u.role].append(u)
    roles = [
        RoleUsage(role=role, calls=len(us),  # type: ignore[arg-type]
                  prompt_tokens=sum(u.prompt_tokens or 0 for u in us),
                  completion_tokens=sum(u.completion_tokens or 0 for u in us),
                  p50_ms=percentile([u.latency_ms for u in us], 0.5) or 0.0,
                  p95_ms=percentile([u.latency_ms for u in us], 0.95) or 0.0)
        for role, us in sorted(usage.items())
    ]
    replies = [ms for r in runs for ms in r.reply_ms]
    return Scorecard(meta=meta, goldens=goldens, prd_rows=rows, llm=roles,
                     reply_p50_ms=percentile(replies, 0.5), reply_p95_ms=percentile(replies, 0.95))


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def render_markdown(card: Scorecard) -> str:
    m = card.meta
    untrusted = sorted(set(m.calibration) - set(m.trusted))
    lines = [
        f"# Alfred eval scorecard — {m.finished_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"model `{m.model}` · Alfred `{m.alfred_commit[:7]}` · home-service `{m.home_service_commit[:7]}` · "
        f"{m.epochs} epochs · judge trusted: {', '.join(m.trusted) or 'none'}"
        + (f" · untrusted: {', '.join(untrusted)}" if untrusted else ""),
        "",
    ]
    for s in m.stacks:
        lines.append(f"- stack `{s.get('suite')}`: boot {s.get('boot_seconds', 0) or 0:.0f} s, "
                     f"first reply {(s.get('first_reply_ms') or 0) / 1000:.1f} s, restarts {s.get('restarts', 0)}")
    lines += ["", "## PRD rows", "", "| PRD row | goldens | pass rate | pass^k |", "|---|---|---|---|"]
    lines += [f"| {r.prd_id} | {len(r.goldens)} | {_pct(r.pass_rate)} | {r.pass_k}/{len(r.goldens)} |"
              for r in card.prd_rows]
    shipped = [g for g in card.goldens if g.status == "shipped"]
    for suite in sorted({g.suite for g in shipped}):
        lines += ["", f"## {suite}", "",
                  "| golden | variants | runs | pass rate | pass^k | flaky | errors | first failing check |",
                  "|---|---|---|---|---|---|---|---|"]
        for g in (g for g in shipped if g.suite == suite):
            first = g.failing[0].replace("|", "\\|")[:160] if g.failing else ""
            lines.append(f"| {g.scenario_id} | {g.variants} | {g.runs} | {_pct(g.pass_rate)} | "
                         f"{'✓' if g.pass_k else '✗'} | {'⚠' if g.flaky else ''} | {g.errors} | {first} |")
    pending = [g for g in card.goldens if g.status == "pending"]
    if pending:
        lines += ["", "## Not yet working (pending)", "", "| golden | PRD rows | pass rate |", "|---|---|---|"]
        lines += [f"| {g.scenario_id} | {', '.join(g.prd)} | {_pct(g.pass_rate)} |" for g in pending]
    lines += ["", "## LLM usage", "", "| role | calls | prompt tok | completion tok | p50 ms | p95 ms |",
              "|---|---|---|---|---|---|"]
    lines += [f"| {u.role} | {u.calls} | {u.prompt_tokens} | {u.completion_tokens} | {u.p50_ms:.0f} | {u.p95_ms:.0f} |"
              for u in card.llm]
    if card.reply_p50_ms is not None:
        lines += ["", f"Reply latency: p50 {card.reply_p50_ms / 1000:.1f} s · p95 {(card.reply_p95_ms or 0) / 1000:.1f} s"]
    return "\n".join(lines) + "\n"


def write_report(card: Scorecard, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path, json_path = out_dir / "report.md", out_dir / "report.json"
    md_path.write_text(render_markdown(card))
    json_path.write_text(card.model_dump_json(indent=2))
    return md_path, json_path
```

- [ ] **Step 4: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_report.py -v && .venv/bin/mypy --strict evals/`
Expected: PASS and clean. If `sample.store` is not a plain dict in this Inspect version, read it with `dict(sample.store or {})`.

- [ ] **Step 5: Commit**

```bash
git add evals/harness/report.py tests/evals/harness/test_report.py
git commit -m "feat(evals): scorecard by golden, PRD row and LLM role"
```

---

### Task 12: Inspect tasks, orchestration, `alfred evals list` and `alfred evals run`

**Files:**
- Create: `evals/harness/tasks.py`, `evals/harness/orchestrate.py`
- Modify: `evals/cli.py`
- Test: `tests/evals/harness/test_tasks.py`, `tests/evals/harness/test_orchestrate.py`, plus additions to `tests/evals/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 3–11.
- Produces:
  - In `tasks.py`:
    - `RunContext(stack, judge, trusted, variants, play_ctx)`.
    - `to_sample(variant) -> Sample`, `verdict(results) -> str`.
    - `async score_evidence(evidence, scenario, judge, trusted) -> tuple[str, list[CheckResult]]`.
    - The solvers `reset_or_recover(ctx)` and `play_scenario(ctx)`, and the scorer `scenario_scorer(ctx)`, named `"scenario_scorer"`.
    - `build_task(suite, variants, ctx, epochs) -> Task`.
  - In `orchestrate.py`:
    - `RunOptions`.
    - `async execute(plan, *, stack_factory, make_ctx, eval_fn, log_dir, epochs) -> tuple[list[EvalLog], list[dict]]`.
    - `async run_suites(opts) -> Path`.
  - In `cli.py`: the `list` and `run` commands.
- **Recovering a dead container:** a stack that is not alive before a sample gets **one** restart per suite. After that, every sample raises `HarnessError` straight away.

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/harness/test_tasks.py
from __future__ import annotations

from pathlib import Path

from inspect_ai import eval_async
from inspect_ai.model import ModelOutput, get_model

from bus.schemas.events import AlfredResponse, UserRequest
from evals.harness.checks.result import CheckResult
from evals.harness.driver import PlayContext
from evals.harness.fake_ha import FakeHA
from evals.harness.judge import Judge
from evals.harness.proxy import LlmProxy
from evals.harness.report import runs_from_logs
from evals.harness.scenario import Scenario, expand_variants
from evals.harness.tasks import RunContext, build_task, verdict
from evals.harness.world import load_world


class FakeStack:
    def __init__(self, alive: list[bool]) -> None:
        self._alive = alive
        self.name = "alfred-eval-test"
        self.restarts = 0

    async def alive(self) -> bool:
        return self._alive.pop(0) if len(self._alive) > 1 else self._alive[0]

    async def restart(self) -> None:
        self.restarts += 1


async def polite(request: UserRequest, timeout: float) -> AlfredResponse:
    return AlfredResponse(source="conscious-engine", channel=request.channel,
                          session_id=request.session_id, text="Good evening, sir.")


def scenario() -> Scenario:
    return Scenario.model_validate({
        "id": "conversation.persona.greeting", "prd": ["1.butler"], "status": "shipped",
        "suite": "conversation", "steps": [{"user": "Good evening, Alfred.", "variants": ["Hello Alfred."]}],
        "expect": [{"reply_contains": {"text": "sir"}},
                   {"judge": {"category": "tone", "rubric": "Does the reply keep a formal butler register?"}}],
    })


def context(stack: FakeStack, judge_says: str = "Formal.\nVERDICT: yes") -> RunContext:
    variants = expand_variants(scenario())
    outputs = [ModelOutput.from_content(model="mockllm/model", content=judge_says)] * 10
    judge = Judge(get_model("mockllm/model", custom_outputs=outputs, memoize=False))
    play_ctx = PlayContext(send=polite, fake_ha=FakeHA(load_world("apartment")),
                           proxy=LlmProxy("http://x"), settle_s=0, restore_settle_s=0)
    return RunContext(stack=stack, judge=judge, trusted={"tone"},  # type: ignore[arg-type]
                      variants={v.sample_id: v for v in variants}, play_ctx=play_ctx)


def test_verdict_rules() -> None:
    ok = CheckResult(name="a", status="pass", reason="")
    bad = CheckResult(name="b", status="fail", reason="")
    err = CheckResult(name="c", status="error", reason="")
    untrusted_bad = CheckResult(name="d", status="fail", reason="", counted=False)
    assert verdict([ok]) == "C"
    assert verdict([ok, bad, err]) == "I"
    assert verdict([ok, err]) == "N"
    assert verdict([ok, untrusted_bad]) == "C"
    assert verdict([untrusted_bad]) == "N"


async def test_a_suite_runs_end_to_end_in_process(tmp_path: Path) -> None:
    ctx = context(FakeStack([True]))
    task = build_task("conversation", list(ctx.variants.values()), ctx, epochs=2)
    logs = await eval_async(task, model="mockllm/model", log_dir=str(tmp_path), max_samples=1)
    runs = runs_from_logs(logs)
    assert len(runs) == 4 and {r.value for r in runs} == {"C"}
    assert {r.sample_id for r in runs} == {"conversation.persona.greeting", "conversation.persona.greeting~1"}


async def test_dead_container_restarts_once_then_errors_fast(tmp_path: Path) -> None:
    stack = FakeStack([False])
    ctx = context(stack)
    task = build_task("conversation", list(ctx.variants.values()), ctx, epochs=1)
    logs = await eval_async(task, model="mockllm/model", log_dir=str(tmp_path),
                            max_samples=1, retry_on_error=1, fail_on_error=False)
    runs = runs_from_logs(logs)
    assert stack.restarts == 1
    assert [r.value for r in runs].count("E") >= 1
    assert all("not running" in (r.error or "") for r in runs if r.value == "E")
```

```python
# tests/evals/harness/test_orchestrate.py
from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.orchestrate import execute
from evals.harness.scenario import Scenario, expand_variants


class RecordingStack:
    def __init__(self) -> None:
        self.started = self.stopped = 0
        self.boot_seconds = 1.0
        self.first_reply_ms = 100.0
        self.restarts = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


def plan() -> dict[str, list]:  # type: ignore[type-arg]
    s = Scenario.model_validate({"id": "demo.a.b", "prd": ["x"], "status": "shipped", "suite": "demo",
                                 "steps": [{"user": "hi"}], "expect": [{"ha_not_called": {}}]})
    return {"demo": expand_variants(s)}


async def test_execute_tears_down_when_eval_raises(tmp_path: Path) -> None:
    stacks: list[RecordingStack] = []

    def factory() -> RecordingStack:
        stacks.append(RecordingStack())
        return stacks[-1]

    async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("inspect crashed")

    with pytest.raises(RuntimeError, match="inspect crashed"):
        await execute(plan(), stack_factory=factory, make_ctx=lambda stack, variants: object(),  # type: ignore[arg-type]
                      eval_fn=boom, log_dir=tmp_path, epochs=1, build_task_fn=lambda *a: "task")  # type: ignore[arg-type]
    assert stacks[0].started == 1 and stacks[0].stopped == 1


async def test_execute_collects_logs_and_stack_meta(tmp_path: Path) -> None:
    async def fake_eval(task, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["max_samples"] == 1 and kwargs["retry_on_error"] == 1
        return ["log"]

    logs, meta = await execute(plan(), stack_factory=RecordingStack,  # type: ignore[arg-type]
                               make_ctx=lambda stack, variants: object(), eval_fn=fake_eval,  # type: ignore[arg-type]
                               log_dir=tmp_path, epochs=1, build_task_fn=lambda *a: "task")
    assert logs == ["log"] and meta[0]["suite"] == "demo" and meta[0]["boot_seconds"] == 1.0
```

Add to `tests/evals/test_cli.py`:

```python
def test_evals_list_shows_scenarios() -> None:
    result = runner.invoke(app, ["evals", "list", "--include-pending"])
    assert result.exit_code == 0, result.output
    assert "home_control.lights.turn_on_named_lamp" in result.output


def test_evals_run_rejects_an_unknown_suite() -> None:
    result = runner.invoke(app, ["evals", "run", "nope"])
    assert result.exit_code == 1 and "unknown suite" in result.output
```

The two CLI tests need the goldens from Task 13. Mark them `@pytest.mark.skip(reason="goldens land in Task 13")` here, and Task 13 removes the marks.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/harness/test_tasks.py tests/evals/harness/test_orchestrate.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `tasks.py`**

```python
# evals/harness/tasks.py
"""Inspect wiring: one Task per suite; solver = driver, scorer = checks + judge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from inspect_ai import Epochs, Task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser, ModelOutput
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver

from evals.harness.checks import run_check
from evals.harness.checks.judge_spec import JudgeSpec
from evals.harness.checks.result import CheckResult
from evals.harness.driver import HarnessError, PlayContext, play
from evals.harness.evidence import Evidence
from evals.harness.judge import Judge, judge_check
from evals.harness.report import SCORER_NAME
from evals.harness.scenario import Scenario, ScenarioVariant, UserStep


@dataclass
class RunContext:
    stack: Any  # evals.harness.stack.Stack; Any so tests can pass a fake
    judge: Judge
    trusted: set[str]
    variants: dict[str, ScenarioVariant]
    play_ctx: PlayContext
    restarts_left: int = 1


def to_sample(variant: ScenarioVariant) -> Sample:
    s = variant.scenario
    first = next((st.user for st in variant.steps if isinstance(st, UserStep)), "(event-driven)")
    return Sample(id=variant.sample_id, input=first, metadata={
        "scenario_id": s.id, "variant": variant.variant, "suite": s.suite, "status": s.status,
        "prd": s.prd, "tags": s.tags, "isolated": s.isolated, "path": s.path,
    })


def verdict(results: list[CheckResult]) -> str:
    counted = [r for r in results if r.counted]
    if any(r.status == "fail" for r in counted):
        return INCORRECT
    if not counted or any(r.status == "error" for r in counted):
        return NOANSWER
    return CORRECT


async def score_evidence(
    evidence: Evidence, scenario: Scenario, judge: Judge, trusted: set[str]
) -> tuple[str, list[CheckResult]]:
    results: list[CheckResult] = []
    for check in scenario.expect:
        if check.name == "judge":
            assert isinstance(check.params, JudgeSpec)
            results.append(await judge_check(judge, evidence, check.params, trusted))
        else:
            results.append(run_check(check.name, check.params, evidence))
    return verdict(results), results


@solver
def reset_or_recover(ctx: RunContext) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        variant = ctx.variants[str(state.sample_id)]
        if variant.scenario.isolated:
            await ctx.stack.restart()
        elif not await ctx.stack.alive():
            if ctx.restarts_left > 0:
                ctx.restarts_left -= 1
                await ctx.stack.restart()
            else:
                raise HarnessError(
                    f"eval container {ctx.stack.name} is not running "
                    f"(see `docker logs {ctx.stack.name}`)"
                )
        return state

    return solve


@solver
def play_scenario(ctx: RunContext) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        variant = ctx.variants[str(state.sample_id)]
        evidence = await play(ctx.play_ctx, variant, state.epoch)
        state.store.set("evidence", evidence.model_dump(mode="json"))
        state.messages = [
            ChatMessageAssistant(content=t.text) if t.role == "alfred"
            else ChatMessageUser(content=t.text if t.role == "user" else f"[home event] {t.text}")
            for t in evidence.transcript
        ]
        last = evidence.replies[-1].text if evidence.replies else ""
        state.output = ModelOutput.from_content(model="alfred", content=last)
        return state

    return solve


@scorer(metrics=[mean()], name=SCORER_NAME)
def scenario_scorer(ctx: RunContext) -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        variant = ctx.variants[str(state.sample_id)]
        evidence = Evidence.model_validate(state.store.get("evidence"))
        value, results = await score_evidence(evidence, variant.scenario, ctx.judge, ctx.trusted)
        return Score(
            value=value,
            explanation="\n".join(f"{r.status.upper()}{'' if r.counted else '*'} {r.name}: {r.reason}"
                                  for r in results),
            metadata={"checks": [r.model_dump() for r in results]},
        )

    return score


def build_task(suite: str, variants: list[ScenarioVariant], ctx: RunContext, epochs: int) -> Task:
    return Task(
        name=suite,
        dataset=[to_sample(v) for v in variants],
        setup=reset_or_recover(ctx),
        solver=play_scenario(ctx),
        scorer=scenario_scorer(ctx),
        epochs=Epochs(epochs, "mean"),
        metadata={"suite": suite},
    )
```

- [ ] **Step 4: Implement `orchestrate.py`**

```python
# evals/harness/orchestrate.py
"""`alfred evals run`: preflight, build, then one stack and one Inspect task per suite."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from inspect_ai import Task, eval_async
from inspect_ai.log import EvalLog

from evals.harness.driver import PlayContext
from evals.harness.fake_ha import FakeHA
from evals.harness.judge import Judge, load_report, make_judge_model
from evals.harness.net import docker_bridge_gateway
from evals.harness.preflight import check_home_service, check_models
from evals.harness.proxy import LlmProxy
from evals.harness.report import RunMeta, render_markdown, runs_from_logs, summarize, write_report
from evals.harness.scenario import ScenarioError, ScenarioVariant, expand_variants, load_suites, select
from evals.harness.stack import Stack, StackConfig
from evals.harness.tasks import RunContext, build_task
from evals.harness.world import load_world

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = REPO_ROOT / "evals" / "logs"


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
    display: str


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
        raise ScenarioError(f"slice 1 serves only the 'apartment' world; goldens ask for {sorted(worlds)}")
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
) -> tuple[list[EvalLog], list[dict[str, Any]]]:
    logs: list[EvalLog] = []
    stacks: list[dict[str, Any]] = []
    for suite, variants in plan.items():
        stack = stack_factory()
        try:
            await stack.start()
            task = build_task_fn(suite, variants, make_ctx(stack, variants), epochs)
            logs += await eval_fn(task, model="mockllm/model", log_dir=str(log_dir), max_samples=1,
                                  retry_on_error=1, fail_on_error=False)
            stacks.append({"suite": suite, "boot_seconds": stack.boot_seconds,
                           "first_reply_ms": stack.first_reply_ms, "restarts": stack.restarts})
        finally:
            await stack.stop()
    return logs, stacks


def _commit(path: Path) -> str:
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def _build_image(home_service: Path) -> None:
    alfredctl = Path(sys.executable).parent / "alfredctl"
    env = {**os.environ, "ALFRED_HOME_SERVICE_DIR": str(home_service)}
    subprocess.run([str(alfredctl), "build", "--runtime", "docker"], check=True, env=env)


async def run_suites(opts: RunOptions) -> Path:
    # eval_async takes no display argument; Inspect reads this on first use.
    os.environ["INSPECT_DISPLAY"] = opts.display
    plan = build_plan(opts)
    async with httpx.AsyncClient(timeout=10) as client:
        await check_models(client, opts.vllm_url, opts.model)
        await check_models(client, f"{opts.embed_url}/v1", opts.embed_model)
    hs_commit = check_home_service(opts.home_service, allow_stale=opts.allow_stale_home_service)
    if opts.build:
        _build_image(opts.home_service)
    run_dir = opts.log_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    calibration = load_report()
    trusted: set[str] = set()
    if calibration is None or calibration.model != opts.model:
        logger.warning("no judge calibration for %s — every judge check is untrusted; "
                       "run `alfred evals calibrate`", opts.model)
    else:
        trusted = calibration.trusted()
    judge = Judge(make_judge_model(opts.model, opts.vllm_url))

    gateway = docker_bridge_gateway()
    fake_ha = FakeHA(load_world("apartment"), host=gateway)
    proxy = LlmProxy(opts.vllm_url.removesuffix("/v1"), host=gateway)
    cfg = StackConfig(model=opts.model, vllm_url=opts.vllm_url, embed_url=opts.embed_url,
                      embed_model=opts.embed_model, work_dir=run_dir / "data",
                      home_service_dir=opts.home_service, keep=opts.keep)

    def make_ctx(stack: Stack, variants: Sequence[ScenarioVariant]) -> RunContext:
        play_ctx = PlayContext(send=stack.send, fake_ha=fake_ha, proxy=proxy,
                               reply_timeout_s=cfg.reply_timeout_s)
        return RunContext(stack=stack, judge=judge, trusted=trusted,
                          variants={v.sample_id: v for v in variants}, play_ctx=play_ctx)

    await fake_ha.start()
    await proxy.start()
    try:
        logs, stacks = await execute(
            plan, stack_factory=lambda: Stack(cfg, fake_ha=fake_ha, proxy=proxy), make_ctx=make_ctx,
            eval_fn=eval_async, log_dir=run_dir, epochs=opts.epochs,
        )
    finally:
        await proxy.stop()
        await fake_ha.stop()

    meta = RunMeta(
        run_dir=str(run_dir), model=opts.model, alfred_commit=_commit(REPO_ROOT),
        home_service_commit=hs_commit, epochs=opts.epochs,
        calibration={c: r.agreement for c, r in (calibration.categories.items() if calibration else [])},
        trusted=sorted(trusted), stacks=stacks,
    )
    card = summarize(runs_from_logs(logs), meta)
    write_report(card, run_dir)
    print(render_markdown(card))
    return run_dir
```

- [ ] **Step 5: Add `list` and `run` to `evals/cli.py`**

```python
DEFAULT_EMBED_URL = "http://localhost:8001"
DEFAULT_EMBED_MODEL = "BAAI/bge-m3"

SuitesArg = Annotated[list[str] | None, typer.Argument(help="Suites (default: all)")]
TagOpt = Annotated[list[str] | None, typer.Option("--tag", help="Only goldens with this tag")]
PendingOpt = Annotated[bool, typer.Option("--include-pending", help="Also pending goldens")]


@evals_app.command("list")
def list_cmd(suites: SuitesArg = None, tag: TagOpt = None, include_pending: PendingOpt = False) -> None:
    """List goldens: id, status, variants and PRD rows."""
    from evals.harness.scenario import ScenarioError, UserStep, load_suites, select

    try:
        loaded = load_suites(suites or None)
    except ScenarioError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    for scenarios in loaded.values():
        for s in select(scenarios, tags=tag or [], include_pending=include_pending):
            variants = 1 + sum(len(st.variants) for st in s.steps if isinstance(st, UserStep))
            typer.echo(f"{s.id:55} {s.status:8} ×{variants}  {', '.join(s.prd)}")


@evals_app.command()
def run(
    suites: SuitesArg = None,
    tag: TagOpt = None,
    include_pending: PendingOpt = False,
    epochs: Annotated[int, typer.Option(min=1, help="Runs per golden")] = 3,
    model: ModelOpt = DEFAULT_MODEL,
    vllm_url: VllmUrlOpt = DEFAULT_VLLM_URL,
    embed_url: Annotated[str, typer.Option(help="Embedding server, without /v1")] = DEFAULT_EMBED_URL,
    embed_model: Annotated[str, typer.Option(help="Embedding model")] = DEFAULT_EMBED_MODEL,
    home_service: Annotated[
        Path | None,
        typer.Option(help="home-service checkout to bundle "
                     "(default: $ALFRED_EVALS_HOME_SERVICE, else the sibling repo)"),
    ] = None,
    allow_stale_home_service: Annotated[bool, typer.Option("--allow-stale-home-service")] = False,
    build: Annotated[bool, typer.Option("--build/--no-build", help="Build the image first")] = True,
    keep: Annotated[bool, typer.Option("--keep", help="Leave containers and data for debugging")] = False,
    display: Annotated[str, typer.Option(help="Inspect display: full | rich | plain | none")] = "full",
) -> None:
    """Boot throwaway stacks and score the goldens. Prints the scorecard."""
    import asyncio
    import os

    from alfredctl import staging
    from evals.harness.orchestrate import LOG_ROOT, RunOptions, run_suites
    from evals.harness.preflight import PreflightError
    from evals.harness.scenario import ScenarioError
    from evals.harness.stack import StackError

    hs = home_service or Path(os.environ.get("ALFRED_EVALS_HOME_SERVICE") or staging.home_service_dir())
    opts = RunOptions(
        suites=list(suites or []), tags=tag or [], include_pending=include_pending, epochs=epochs,
        model=model, vllm_url=vllm_url, embed_url=embed_url, embed_model=embed_model,
        home_service=hs, allow_stale_home_service=allow_stale_home_service, build=build,
        keep=keep, log_root=LOG_ROOT, display=display,
    )
    try:
        run_dir = asyncio.run(run_suites(opts))
    except (ScenarioError, PreflightError, StackError) as exc:
        typer.echo(f"alfred evals: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"logs and report: {run_dir}  (inspect view --log-dir {run_dir})")
```

Add `from pathlib import Path` at the top of `evals/cli.py`. In `run`, the preflight must come before any build: `run_suites` already orders it that way, so do not move `_build_image` earlier.

- [ ] **Step 6: Run the tests and type-check**

Run: `.venv/bin/python -m pytest tests/evals -q && .venv/bin/mypy --strict evals/ alfred_cli/`
Expected: PASS (apart from the two CLI tests skipped until Task 13) and clean.

- [ ] **Step 7: Commit**

```bash
git add evals/harness/tasks.py evals/harness/orchestrate.py evals/cli.py tests/evals
git commit -m "feat(evals): alfred evals run — Inspect tasks over throwaway stacks"
```

---

### Task 13: The goldens — `conversation` and `home_control`

**Files:**
- Create: the files under `evals/suites/conversation/` and `evals/suites/home_control/` listed below
- Modify: `tests/evals/test_cli.py` (remove the two skip marks)
- Test: `tests/evals/test_goldens_load.py`

**Interfaces:**
- Consumes: the scenario schema (Task 4) and the apartment world (Task 5).
- Produces: the suites `conversation` (8 goldens) and `home_control` (13 goldens: 11 shipped, 2 pending). Task 14 cites their PRD ids.

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/test_goldens_load.py
from __future__ import annotations

from evals.harness.scenario import HaEventStep, load_suites
from evals.harness.world import load_world


def test_every_golden_loads_and_names_real_entities() -> None:
    world_ids = {e.entity_id for e in load_world("apartment").entities}
    suites = load_suites(["conversation", "home_control"])
    assert len(suites["conversation"]) >= 8 and len(suites["home_control"]) >= 13
    for scenarios in suites.values():
        for s in scenarios:
            for step in s.steps:
                if isinstance(step, HaEventStep):
                    assert step.ha_event.entity_id in world_ids, s.path
            for check in s.expect:
                entity = getattr(check.params, "entity_id", None)
                for e in [entity] if isinstance(entity, str) else (entity or []):
                    assert e in world_ids, f"{s.path}: {e}"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/evals/test_goldens_load.py -v`
Expected: FAIL with `ScenarioError: unknown suite(s)`.

- [ ] **Step 3: Write the conversation goldens**

```yaml
# evals/suites/conversation/persona_greeting.yaml
id: conversation.persona.greeting
prd: [1.butler]
status: shipped
tags: [persona]
steps:
  - user: "Good evening, Alfred."
    variants: ["Hello Alfred."]
expect:
  - judge:
      category: tone
      rubric: "Does the reply keep a formal, courteous butler register?"
```

```yaml
# evals/suites/conversation/persona_casual_user.yaml
id: conversation.persona.casual_user
prd: [1.butler]
status: shipped
tags: [persona]
steps:
  - user: "yo alfred what's good"
    variants: ["sup dude, anything going on?"]
expect:
  - reply_not_contains: {regex: "\\b(yo|dude|sup)\\b"}
  - judge:
      category: tone
      rubric: "Does the reply keep a formal, courteous butler register even though the user was casual?"
```

```yaml
# evals/suites/conversation/honesty_unsupported_request.yaml
id: conversation.honesty.unsupported_request
prd: [1.butler]
status: shipped
tags: [honesty]
steps:
  - user: "Book me a table at Nobu for eight tonight."
    variants: ["Can you reserve dinner for two at Nobu this evening?"]
expect:
  - ha_not_called: {}
  - judge:
      category: faithfulness
      rubric: "Does the reply make clear that Alfred cannot book the table, without claiming it did?"
      reference: "I'm afraid I can't make restaurant reservations, sir. Shall I set a reminder for you to call Nobu?"
```

```yaml
# evals/suites/conversation/session_recall_within_session.yaml
id: conversation.session.recall_within_session
prd: [4.1.sessions]
status: shipped
tags: [sessions]
steps:
  - user: "My sister Priya is visiting on Saturday."
  - user: "Who did I say is visiting?"
expect:
  - reply_contains: {text: "Priya"}
  - judge:
      category: faithfulness
      rubric: "Does the reply correctly say that the user's sister Priya is visiting on Saturday?"
```

```yaml
# evals/suites/conversation/session_followup_refers_back.yaml
id: conversation.session.followup_refers_back
prd: [4.1.sessions, 4.4.lights-scenes]
status: shipped
tags: [sessions, lights]
steps:
  - user: "Turn on the bedroom lamp."
  - user: "Actually, make it half brightness."
expect:
  - ha_called:
      domain: light
      service: turn_on
      entity_id: light.bedroom_lamp
      data: {brightness_pct: {approx: 50, tol: 5}}
      after_step: 1
  - ha_state:
      entity_id: light.bedroom_lamp
      state: "on"
      attributes: {brightness: {approx: 128, tol: 13}}
```

```yaml
# evals/suites/conversation/signal_multi_turn.yaml
id: conversation.signal.multi_turn
prd: [4.1.signal, 4.1.sessions]
status: shipped
tags: [signal, sessions]
as: {who: sir, channel: signal}
steps:
  - user: "The plumber is coming at three tomorrow."
  - user: "When did I say the plumber is coming?"
expect:
  - reply_contains: {any: ["three", "3 pm", "3pm", "15:00", "3:00"]}
  - judge:
      category: faithfulness
      rubric: "Does the reply say the plumber is coming at three o'clock tomorrow?"
```

```yaml
# evals/suites/conversation/signal_home_question.yaml
id: conversation.signal.home_question
prd: [4.1.signal, 4.4.live-state]
status: shipped
tags: [signal]
as: {who: sir, channel: signal}
steps:
  - user: "Is the front door locked?"
expect:
  - reply_contains: {regex: "\\blocked\\b"}
  - reply_not_contains: {text: "unlocked"}
  - ha_not_called: {}
  - judge:
      category: answered
      rubric: "Does the reply directly say whether the front door is locked?"
```

```yaml
# evals/suites/conversation/warmup_reply_latency.yaml
# Steady-state reply time. The scorecard's stack line reports the first reply after boot.
id: conversation.warmup.reply_latency
prd: [4.7.warmup]
status: shipped
tags: [latency]
steps:
  - user: "Thank you, Alfred."
expect:
  - latency: {metric: reply_ms, max: 15000}
  - judge:
      category: tone
      rubric: "Does the reply acknowledge the thanks courteously, in a butler's register?"
```

- [ ] **Step 4: Write the home_control goldens**

```yaml
# evals/suites/home_control/lights_turn_on_named_lamp.yaml
id: home_control.lights.turn_on_named_lamp
prd: [4.4.lights-scenes, principle.3]
status: shipped
tags: [lights]
steps:
  - user: "Turn on the bedroom lamp."
    variants: ["Bedroom lamp on, please.", "Could you switch on the lamp in the bedroom?"]
expect:
  - ha_called: {domain: light, service: turn_on, entity_id: light.bedroom_lamp}
  - ha_state: {entity_id: light.bedroom_lamp, state: "on"}
  - ha_not_called: {domain: light, service: turn_off}
  - judge:
      category: answered
      rubric: "Does the reply confirm that the bedroom lamp is on?"
```

```yaml
# evals/suites/home_control/lights_turn_off_room.yaml
id: home_control.lights.turn_off_room
prd: [4.4.lights-scenes]
status: shipped
tags: [lights]
steps:
  - user: "Turn off the lights in the living room."
    variants: ["Kill the living room lights."]
expect:
  - ha_state: {entity_id: light.living_room_lamp, state: "off"}
  - ha_not_called: {entity_id: light.kitchen_pendants}
```

```yaml
# evals/suites/home_control/lights_dim_to_percent.yaml
id: home_control.lights.dim_to_percent
prd: [4.4.lights-scenes]
status: shipped
tags: [lights]
steps:
  - user: "Dim the kitchen pendants to 30 percent."
    variants: ["Set the kitchen pendants to 30% brightness."]
expect:
  - ha_called:
      domain: light
      service: turn_on
      entity_id: light.kitchen_pendants
      data: {brightness_pct: {approx: 30, tol: 2}}
  - llm_tool_args: {tool: home.light_turn_on, args: {brightness_pct: {approx: 30, tol: 2}}}
```

```yaml
# evals/suites/home_control/scenes_movie_night.yaml
id: home_control.scenes.movie_night
prd: [4.4.lights-scenes]
status: shipped
tags: [scenes]
steps:
  - user: "Set the movie night scene."
    variants: ["Movie night, please."]
expect:
  - ha_called: {domain: scene, service: turn_on, entity_id: scene.movie_night}
```

```yaml
# evals/suites/home_control/devices_coffee_maker.yaml
id: home_control.devices.coffee_maker
prd: [principle.3]
status: shipped
tags: [devices]
steps:
  - user: "Turn on the coffee maker."
    variants: ["Start the coffee machine."]
expect:
  - ha_called: {domain: switch, service: turn_on, entity_id: switch.coffee_maker}
```

```yaml
# evals/suites/home_control/live_state_front_door_locked.yaml
id: home_control.live_state.front_door_locked
prd: [4.4.live-state]
status: shipped
tags: [live-state]
steps:
  - user: "Is the front door locked?"
    variants: ["Did I lock the front door?"]
expect:
  - reply_contains: {regex: "\\blocked\\b"}
  - reply_not_contains: {text: "unlocked"}
  - ha_not_called: {}
```

```yaml
# evals/suites/home_control/live_state_reflects_recent_change.yaml
id: home_control.live_state.reflects_recent_change
prd: [4.4.live-state]
status: shipped
tags: [live-state]
steps:
  - ha_event: {entity_id: light.living_room_ceiling, state: "on", attributes: {brightness: 200}}
  - user: "Is the living room ceiling light on?"
expect:
  - judge:
      category: faithfulness
      rubric: "Does the reply say that the living room ceiling light is currently on?"
```

```yaml
# evals/suites/home_control/live_state_temperature.yaml
id: home_control.live_state.temperature
prd: [4.4.live-state]
status: shipped
tags: [live-state]
steps:
  - user: "What's the temperature in the living room?"
expect:
  - reply_contains: {regex: "21([.,]5)?|twenty[- ]one"}
  - ha_not_called: {}
```

```yaml
# evals/suites/home_control/live_state_which_lights_on.yaml
id: home_control.live_state.which_lights_on
prd: [4.4.live-state]
status: shipped
tags: [live-state]
steps:
  - user: "Which lights are on right now?"
expect:
  - ha_not_called: {}
  - reply_contains: {regex: "living room lamp"}
  - reply_contains: {regex: "kitchen pendant"}
  - judge:
      category: faithfulness
      rubric: "Does the reply name the living room lamp and the kitchen pendants as on, without claiming the bedroom lamp or the living room ceiling light is on?"
```

```yaml
# evals/suites/home_control/negative_unknown_device.yaml
id: home_control.negative.unknown_device
prd: [4.4.lights-scenes]
status: shipped
tags: [negative]
steps:
  - user: "Turn on the bathroom fan."
expect:
  - ha_not_called: {}
  - judge:
      category: faithfulness
      rubric: "Does the reply say there is no bathroom fan it can control (or ask which device was meant), without claiming to have turned something on?"
```

```yaml
# evals/suites/home_control/negative_ambiguous_dim.yaml
id: home_control.negative.ambiguous_dim
prd: [4.4.lights-scenes]
status: shipped
tags: [negative]
steps:
  - user: "Dim it a bit."
expect:
  - ha_not_called: {}
  - judge:
      category: answered
      rubric: "Does the reply ask which light the user means instead of acting?"
```

```yaml
# evals/suites/home_control/discovery_room_inventory.yaml
id: home_control.discovery.room_inventory
prd: [4.4.device-discovery]
status: pending
tags: [discovery]
steps:
  - user: "What can you control in the kitchen?"
expect:
  - reply_contains: {regex: "pendant"}
  - reply_contains: {regex: "coffee"}
```

```yaml
# evals/suites/home_control/control_surface_media_volume.yaml
id: home_control.control_surface.media_volume
prd: [4.4.control-surface]
status: pending
tags: [control-surface]
steps:
  - user: "Set the living room TV volume to 20 percent."
expect:
  - ha_called:
      domain: media_player
      service: volume_set
      entity_id: media_player.living_room_tv
      data: {volume_level: {approx: 0.2, tol: 0.02}}
```

- [ ] **Step 5: Run the tests**

Remove the two `skip` marks in `tests/evals/test_cli.py`, then run:

```bash
.venv/bin/python -m pytest tests/evals/test_goldens_load.py tests/evals/test_cli.py -v
.venv/bin/alfred evals list --include-pending
```
Expected: PASS. The list shows 21 goldens, 2 of them pending.

- [ ] **Step 6: Commit**

```bash
git add evals/suites tests/evals/test_goldens_load.py tests/evals/test_cli.py
git commit -m "test(evals): conversation and home_control goldens"
```

---

### Task 14: The PRD coverage map and its test

**Files:**
- Create: `evals/harness/coverage.py`, `evals/coverage.yaml`
- Test: `tests/evals/test_prd_coverage.py`

**Interfaces:**
- Consumes: `load_suites` and `Scenario` (Task 4), and the goldens (Task 13).
- Produces:
  - `PLANNED_SUITES`, `PrdRow(section, text)` and `parse_prd(text) -> list[PrdRow]`.
  - `CoverageEntry`, `HeadingEntry`, `Coverage` and `load_coverage(path) -> Coverage`.
  - `coverage_problems(prd_text, coverage, suites, repo_root) -> list[str]`.
  - The paths `PRD_PATH` and `COVERAGE_PATH`.
- **How rows are matched:** an entry matches a row when the sections are equal and the row's text, with whitespace collapsed, **starts with** the entry's `prd` text.
  - §3: the row text is the bold principle name.
  - §4.x: the first cell of the table row.
  - §7: the first cell of the table row.
  - §1 and §6 are checked through `headings`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/test_prd_coverage.py
from __future__ import annotations

from pathlib import Path

from evals.harness.coverage import (
    COVERAGE_PATH,
    PRD_PATH,
    REPO_ROOT,
    Coverage,
    coverage_problems,
    load_coverage,
    parse_prd,
)
from evals.harness.scenario import load_suites

MINI_PRD = """# Alfred
## 1. What is Alfred
text
## 3. Product principles
1. **Proactive, not intrusive.** Alfred acts.
2. **Local-first and private.** Data stays.
## 4. Capability Catalog
### 4.1 Conversation & channels
| Capability | Status | Reference |
|---|---|---|
| Signal messaging (inbound requests + outbound notifications) | Shipped | x |
## 7. Success criteria
| Dimension | Bar |
|---|---|
| Reflex latency | < 500 ms |
"""


def test_parse_prd_finds_principles_rows_and_criteria() -> None:
    rows = parse_prd(MINI_PRD)
    assert [(r.section, r.text) for r in rows] == [
        ("3", "Proactive, not intrusive."),
        ("3", "Local-first and private."),
        ("4.1", "Signal messaging (inbound requests + outbound notifications)"),
        ("7", "Reflex latency"),
    ]


def test_problems_name_unmapped_rows_and_unknown_suites(tmp_path: Path) -> None:
    coverage = Coverage.model_validate({
        "rows": [
            {"id": "principle.1", "section": "3", "prd": "Proactive, not intrusive.", "pending_suites": ["notifications"]},
            {"id": "4.1.signal", "section": "4.1", "prd": "Signal messaging", "suites": ["convo"]},
            {"id": "7.reflex-latency", "section": "7", "prd": "Reflex latency", "pending_suites": ["reflex"]},
            {"id": "4.1.ghost", "section": "4.1", "prd": "Ghost row", "not_llm": "x", "tests": ["nope/"]},
        ],
        "headings": [{"id": "1.butler", "heading": "1. What is Alfred", "pending_suites": ["conversation"]}],
    })
    problems = "\n".join(coverage_problems(MINI_PRD, coverage, {}, tmp_path))
    assert "Local-first and private." in problems  # unmapped principle
    assert "convo" in problems  # unknown suite
    assert "4.1.ghost matches no PRD row" in problems
    assert "nope/" in problems  # missing test path


def test_the_real_prd_is_fully_mapped() -> None:
    problems = coverage_problems(PRD_PATH.read_text(), load_coverage(COVERAGE_PATH), load_suites(), REPO_ROOT)
    assert problems == [], "\n".join(problems)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/evals/test_prd_coverage.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `coverage.py`**

```python
# evals/harness/coverage.py
"""Every LLM-related PRD requirement maps to goldens, tests, or a stated not_llm reason.

docs/PRD.md says any PR that changes a capability row updates that row; this module
(and tests/evals/test_prd_coverage.py) make the same PR update the eval map too.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.harness.scenario import Scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
PRD_PATH = REPO_ROOT / "docs" / "PRD.md"
COVERAGE_PATH = REPO_ROOT / "evals" / "coverage.yaml"
PLANNED_SUITES = (
    "conversation", "home_control", "reflex", "triggers", "notifications", "attention", "memory",
    "integrations", "guest_boundary", "critical_actions", "privacy", "cross_domain", "voice",
)
_SECTION = re.compile(r"^## (\d+)\. ")
_SUBSECTION = re.compile(r"^### (\d+\.\d+) ")
_PRINCIPLE = re.compile(r"^\d+\. \*\*(.+?)\*\*")
_HEADER_CELLS = {"Capability", "Dimension"}


class PrdRow(BaseModel):
    section: str
    text: str


def _norm(text: str) -> str:
    return " ".join(text.split())


def parse_prd(text: str) -> list[PrdRow]:
    rows: list[PrdRow] = []
    section: str | None = None
    for line in text.splitlines():
        if m := _SECTION.match(line):
            section = m.group(1)
            continue
        if m := _SUBSECTION.match(line):
            section = m.group(1)
            continue
        if section == "3" and (m := _PRINCIPLE.match(line)):
            rows.append(PrdRow(section="3", text=m.group(1)))
        elif section and (section.startswith("4.") or section == "7") and line.startswith("| "):
            cell = line.split("|")[1].strip()
            if cell and cell not in _HEADER_CELLS:
                rows.append(PrdRow(section=section, text=_norm(cell)))
    return rows


class _Mapped(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    suites: list[str] = Field(default_factory=list)
    pending_suites: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    not_llm: str | None = None
    elsewhere: str | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        evaluated = bool(self.suites or self.pending_suites)
        if evaluated == (self.not_llm is not None):
            raise ValueError(f"{self.id}: give suites/pending_suites or not_llm, not both or neither")
        if self.not_llm is not None and not (self.tests or self.elsewhere):
            raise ValueError(f"{self.id}: a not_llm row names its tests or where it is tested")
        return self


class CoverageEntry(_Mapped):
    section: str
    prd: str


class HeadingEntry(_Mapped):
    heading: str


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[CoverageEntry]
    headings: list[HeadingEntry] = Field(default_factory=list)


def load_coverage(path: Path = COVERAGE_PATH) -> Coverage:
    return Coverage.model_validate(yaml.safe_load(path.read_text()))


def coverage_problems(
    prd_text: str, coverage: Coverage, suites: dict[str, list[Scenario]], repo_root: Path
) -> list[str]:
    problems: list[str] = []
    rows = parse_prd(prd_text)

    def matches(entry: CoverageEntry, row: PrdRow) -> bool:
        return entry.section == row.section and row.text.startswith(_norm(entry.prd))

    for row in rows:
        hits = [e.id for e in coverage.rows if matches(e, row)]
        if len(hits) != 1:
            problems.append(f"PRD §{row.section} row {row.text[:70]!r} matches {hits or 'no entry'}")
    for entry in coverage.rows:
        if not any(matches(entry, row) for row in rows):
            problems.append(f"{entry.id} matches no PRD row (§{entry.section} {entry.prd!r})")

    entries: list[_Mapped] = [*coverage.rows, *coverage.headings]
    ids = [e.id for e in entries]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"duplicate coverage id {dup}")
    headings = {line[3:].strip() for line in prd_text.splitlines() if line.startswith("## ")}
    for h in coverage.headings:
        if h.heading not in headings:
            problems.append(f"{h.id}: PRD has no heading '## {h.heading}'")

    for e in entries:
        for suite in [*e.suites, *e.pending_suites]:
            if suite not in PLANNED_SUITES:
                problems.append(f"{e.id}: unknown suite {suite!r}")
        for suite in e.suites:
            if suite not in suites:
                if suite in PLANNED_SUITES:
                    problems.append(f"{e.id}: suite {suite!r} is not built; list it under pending_suites")
            elif not any(e.id in s.prd for s in suites[suite]):
                problems.append(f"{e.id}: no golden in {suite!r} cites it")
        for test in e.tests:
            if not (repo_root / test).exists():
                problems.append(f"{e.id}: test path {test} does not exist")

    known = set(ids)
    for scenarios in suites.values():
        for s in scenarios:
            for prd_id in s.prd:
                if prd_id not in known:
                    problems.append(f"{s.path}: cites {prd_id!r}, which coverage.yaml does not define")
    return problems
```

- [ ] **Step 4: Write `evals/coverage.yaml`**

```yaml
# Every PRD requirement → goldens (suites), not-yet-built suites (pending_suites),
# existing tests, or a not_llm reason. `prd` is the leading text of the PRD row; the
# coverage test fails when a row is added, reworded or left unmapped (docs/evals.md).
rows:
  # §3 Product principles
  - {id: principle.1, section: "3", prd: "Proactive, not intrusive.", pending_suites: [notifications, memory]}
  - {id: principle.2, section: "3", prd: "Local-first and private.", pending_suites: [privacy]}
  - {id: principle.3, section: "3", prd: "Everything describes itself.", suites: [home_control]}
  - {id: principle.4, section: "3", prd: "Deterministic and auditable.", not_llm: "typed bus schemas", tests: [tests/bus]}
  - {id: principle.5, section: "3", prd: "Learns routines; doesn't demand programming.", pending_suites: [memory, triggers]}
  - {id: principle.6, section: "3", prd: "Sovereign services.", not_llm: "process isolation via the SDK", tests: [sdk/tests]}
  # §4.1 Conversation & channels
  - {id: 4.1.pwa-room, section: "4.1", prd: "Web app — a phone-first PWA", not_llm: "phone UI, gates and the Door", tests: [web/src/room, web/src/door, web/src/gates]}
  - {id: 4.1.workshop-activity, section: "4.1", prd: "Web app — the Workshop: the Activity bench", not_llm: "telemetry UI", tests: [web/src/workshop/ActivityBench.test.tsx, web/src/sheets/WhySheet.test.tsx]}
  - {id: 4.1.workshop-benches, section: "4.1", prd: "Web app — the Workshop's other three benches", not_llm: "management UI", tests: [web/src/workshop/MemoryBench.test.tsx, web/src/workshop/TriggersBench.test.tsx, web/src/workshop/SystemBench.test.tsx]}
  - {id: 4.1.ios-app, section: "4.1", prd: "Native iOS app", not_llm: "separate client", elsewhere: "alfred-ios repo"}
  - {id: 4.1.signal, section: "4.1", prd: "Signal messaging", suites: [conversation]}
  - {id: 4.1.voice-browser, section: "4.1", prd: "Voice in the browser/app", pending_suites: [voice]}
  - {id: 4.1.satellites, section: "4.1", prd: "Physical wake-word satellites", pending_suites: [voice]}
  - {id: 4.1.speaker-id, section: "4.1", prd: "Speaker identification", pending_suites: [voice]}
  - {id: 4.1.sessions, section: "4.1", prd: "Multi-step conversations with session continuity", suites: [conversation]}
  # §4.2 Proactivity & triggers
  - {id: 4.2.dynamic-triggers, section: "4.2", prd: "Dynamic triggers created by conversation", pending_suites: [triggers]}
  - {id: 4.2.sensor-triggers, section: "4.2", prd: "Sensor-driven triggers on live home state", pending_suites: [triggers]}
  - {id: 4.2.fast-reminders, section: "4.2", prd: "Sub-5-second reminder firing", pending_suites: [triggers]}
  - {id: 4.2.client-timezone, section: "4.2", prd: "Client-timezone awareness", pending_suites: [triggers]}
  - {id: 4.2.relative-reminders, section: "4.2", prd: "Relative reminders", pending_suites: [triggers]}
  - {id: 4.2.proactive-notifications, section: "4.2", prd: "Proactive notifications with urgency levels", pending_suites: [notifications]}
  - {id: 4.2.notification-delivery, section: "4.2", prd: "Delivery to Signal, web", pending_suites: [notifications]}
  - {id: 4.2.attention-set, section: "4.2", prd: "Reflex \"attention set\"", pending_suites: [attention]}
  # §4.3 Memory
  - {id: 4.3.episodic, section: "4.3", prd: "Episodic memory", pending_suites: [memory]}
  - {id: 4.3.semantic, section: "4.3", prd: "Semantic memory", pending_suites: [memory]}
  - {id: 4.3.procedural, section: "4.3", prd: "Procedural memory", pending_suites: [memory]}
  - {id: 4.3.librarian, section: "4.3", prd: "Nightly librarian consolidation", pending_suites: [memory]}
  - {id: 4.3.decay, section: "4.3", prd: "Contextual decay", pending_suites: [memory], tests: [tests/evals/test_memory_runner.py]}
  - {id: 4.3.two-stage-recall, section: "4.3", prd: "Two-stage recall", pending_suites: [memory]}
  - {id: 4.3.significance, section: "4.3", prd: "Significance scoring", pending_suites: [memory]}
  - {id: 4.3.passive-observation, section: "4.3", prd: "Passive observation", pending_suites: [memory]}
  - {id: 4.3.s2-observes-s1, section: "4.3", prd: "System 2 observation of System 1", pending_suites: [reflex, memory]}
  # §4.4 Smart home
  - {id: 4.4.lights-scenes, section: "4.4", prd: "Lights and scenes control", suites: [home_control], pending_suites: [reflex]}
  - {id: 4.4.ha-onboarding, section: "4.4", prd: "Token-to-live Home Assistant onboarding", not_llm: "credential flow", tests: [tests/core/channels/test_service_credentials.py]}
  - {id: 4.4.device-discovery, section: "4.4", prd: "Full-home device discovery", suites: [home_control]}
  - {id: 4.4.control-surface, section: "4.4", prd: "Generated control surface", suites: [home_control]}
  - {id: 4.4.live-state, section: "4.4", prd: "Live state streaming", suites: [home_control]}
  - {id: 4.4.tiered-autonomy, section: "4.4", prd: "Tiered autonomy", pending_suites: [reflex]}
  - {id: 4.4.critical-confirmation, section: "4.4", prd: "Confirmation required for critical actions", pending_suites: [critical_actions]}
  # §4.5 Integrations & credentials
  - {id: 4.5.adapters, section: "4.5", prd: "Weather, Apple Calendar", pending_suites: [integrations]}
  - {id: 4.5.secure-credentials, section: "4.5", prd: "Secure credential storage", pending_suites: [privacy], tests: [tests/shared/test_secrets.py]}
  - {id: 4.5.service-credentials, section: "4.5", prd: "Sovereign services declare credential needs", not_llm: "credential delivery", tests: [tests/core/channels/test_service_credentials.py]}
  - {id: 4.5.calendar-management, section: "4.5", prd: "Calendar event creation/management", pending_suites: [integrations]}
  # §4.6 Security & privacy
  - {id: 4.6.passkeys, section: "4.6", prd: "Passkey (WebAuthn) login", not_llm: "authentication", tests: [tests/integration/test_webauthn_flow.py]}
  - {id: 4.6.trusted-network, section: "4.6", prd: "Trusted-network gating", not_llm: "network gate", tests: [tests/core/channels/test_trusted_network.py]}
  - {id: 4.6.session-management, section: "4.6", prd: "Session and passkey management", not_llm: "admin routes", tests: [tests/core/channels/test_admin_api.py]}
  - {id: 4.6.identity-confidence, section: "4.6", prd: "Identity confidence levels per channel", pending_suites: [guest_boundary], tests: [tests/core/conscious/test_identity_local_trust.py]}
  - {id: 4.6.guest-choices, section: "4.6", prd: "Guest access choices captured at onboarding", pending_suites: [guest_boundary], tests: [tests/core/channels/test_onboarding_defaults.py]}
  - {id: 4.6.guest-enforcement, section: "4.6", prd: "Guest boundary enforcement", pending_suites: [guest_boundary]}
  - {id: 4.6.cost-cap, section: "4.6", prd: "Daily cloud-spend cap", not_llm: "budget accounting", tests: [tests/core/conscious/test_cost.py]}
  # §4.7 Operations & quality
  - {id: 4.7.runner, section: "4.7", prd: "Unified runner", not_llm: "process supervision", tests: [tests/runner/test_supervisor.py]}
  - {id: 4.7.admin-api, section: "4.7", prd: "Admin API", not_llm: "HTTP API", tests: [tests/core/channels/test_admin_api.py]}
  - {id: 4.7.phone-ops, section: "4.7", prd: "…surfaced on the phone", not_llm: "phone UI", tests: [web/src/workshop]}
  - {id: 4.7.eval-harness, section: "4.7", prd: "Eval harness", not_llm: "this harness", tests: [tests/evals]}
  - {id: 4.7.warmup, section: "4.7", prd: "Model warmup at startup", suites: [conversation]}
  - {id: 4.7.service-health, section: "4.7", prd: "Self-describing health for external services", not_llm: "health reporting", tests: [web/src/workshop/SystemSections.test.tsx]}
  - {id: 4.7.alfredctl, section: "4.7", prd: "One-command containerized deployment", not_llm: "launcher", tests: [tests/alfredctl]}
  # §7 Success criteria
  - {id: 7.reflex-latency, section: "7", prd: "Reflex latency", pending_suites: [reflex]}
  - {id: 7.reminder-latency, section: "7", prd: "Reminder latency", pending_suites: [triggers]}
  - {id: 7.onboarding, section: "7", prd: "Onboarding", not_llm: "credential flow", tests: [tests/core/channels/test_service_credentials.py]}
  - {id: 7.proactivity-quality, section: "7", prd: "Proactivity quality", pending_suites: [memory, notifications]}
  - {id: 7.cost, section: "7", prd: "Cost", not_llm: "budget accounting", tests: [tests/core/conscious/test_cost.py]}
  - {id: 7.memory-quality, section: "7", prd: "Memory quality", pending_suites: [memory], tests: [tests/evals/test_memory_runner.py]}
  - {id: 7.sovereignty, section: "7", prd: "Sovereignty", not_llm: "process isolation via the SDK", tests: [sdk/tests]}
headings:
  - {id: 1.butler, heading: "1. What is Alfred", suites: [conversation]}
  - {id: 6.cross-domain, heading: "6. Why Alfred is different", pending_suites: [cross_domain]}
```

- [ ] **Step 5: Run the tests and fix any mapping problems they name**

Run: `.venv/bin/python -m pytest tests/evals/test_prd_coverage.py -v && .venv/bin/mypy --strict evals/`
Expected: PASS and clean. If `test_the_real_prd_is_fully_mapped` lists problems, they come from `docs/PRD.md` wording that drifted after this plan was written. Fix the `prd:` prefixes or test paths in `coverage.yaml` to match the current PRD, and never edit the PRD to fit the map. If a listed test path no longer exists, point the entry at the file that now covers that row.

- [ ] **Step 6: Commit**

```bash
git add evals/harness/coverage.py evals/coverage.yaml tests/evals/test_prd_coverage.py
git commit -m "test(evals): map every PRD requirement to goldens or tests"
```

---

### Task 15: Documentation

**Files:**
- Create: `docs/evals.md`
- Delete: `docs/evals-runner.md`
- Modify: `docs/architecture.md`, `docs/PRD.md` (the §4.7 eval-harness row and the "statuses current as of" date), `CLAUDE.md` (Key Paths and the Workflow mypy line), `docs/superpowers/specs/2026-10-06-prd-eval-suite-design.md` (CLI name, dependency wording, the `ha_state` check)

- [ ] **Step 1: Find every stale reference**

Run: `grep -rnE "evals-runner|python -m evals (run|list|compare|runs|regression|conscious|demo|capture-context)|alfred-evals (run|list|calibrate)" docs README.md CLAUDE.md evals | grep -v superpowers/plans`

(`~/.local/share/alfred-evals/` and the `alfred-evals` runner label keep that name. Only the command changed.)
Expected: a list of lines to fix in Steps 2 and 3. Plans are history, so leave them alone.

- [ ] **Step 2: Write `docs/evals.md`**

Write it in the style of `docs/sdk.md`, with these sections in this order:
1. **Overview.** What the suite proves, and that every LLM role runs on vLLM.
2. **Architecture.** The spec's mermaid flowchart, and one paragraph each on the fake HA, the proxy, the driver and the stack.
3. **Running locally.**
   - One-time setup: `uv sync --all-extras`, and a home-service checkout at `origin/main`, for example `git -C ~/code/alfred-deploy/home-service fetch origin main && git -C ~/code/alfred-deploy/home-service worktree add --detach ~/code/.worktrees/home-service/evals-main origin/main`. Set `ALFRED_EVALS_HOME_SERVICE` or pass `--home-service`.
   - `alfred evals calibrate`.
   - `alfred evals list --include-pending`.
   - `alfred evals run home_control conversation`. Mention `--epochs`, `--tag`, `--include-pending`, `--no-build` and `--keep`.
4. **Reading results.** `report.md`, `inspect view --log-dir evals/logs/<run>`, the `C/I/N/E` values, pass rate, pass^k, flaky, and untrusted judge checks (marked `*` in the explanation).
5. **Writing a golden.**
   - The YAML schema (steps, `as`, `variants`, `status`, `isolated`).
   - The quoted-`"on"` gotcha.
   - The step-index conventions.
   - Three goldens per row (happy, edge, negative).
   - Where to cite PRD ids.
6. **Checks.** A table of every check in `DETERMINISTIC` plus `judge`, with params and pass conditions, copied from the params models.
7. **The judge and calibration.** Categories, the 85% trust threshold, where the report is stored, and that changing the model invalidates it.
8. **PRD coverage.** `evals/coverage.yaml`, what the coverage test enforces, and how to map a new row.
9. **Safety.** The fake credentials, no `.env`, loopback ports, the proxy concurrency cap, one container at a time.
10. **Key paths.** A table of `evals/harness/*`.
11. **Slices still to come.** One line each for slices 2–7 from the spec.

- [ ] **Step 3: Update the other docs**

- `git rm docs/evals-runner.md`.
- `docs/architecture.md`: add the eval harness to the system diagram. A host-side "alfred evals" node writes to `alfred:user:requests` and reads `alfred:user:responses`. Next to it are "fake HA", which home-service connects to, and "LLM proxy", which System 1, System 2 and the Librarian call. Add one line pointing at `docs/evals.md`.
- `docs/PRD.md` §4.7: in the "Eval harness" row, change the description to "Eval harness: utterance-level goldens against the real stack, deterministic checks plus a calibrated judge, PRD coverage enforced". Change the reference to `docs/evals.md`, and bump "Capability statuses current as of" to the commit date.
- **If the row text changes,** update its `prd:` prefix in `evals/coverage.yaml`, the `4.7.eval-harness` entry, and run the coverage test.
- `CLAUDE.md`:
  - In Key Paths, replace the `evals/memory/` line with two lines: `evals/harness/` (the PRD eval harness: `alfred evals run|list|calibrate`; see `docs/evals.md`) and `evals/memory/` (unchanged text).
  - In the Workflow mypy command, add `alfred_cli/`.
  - In "Running the System", replace the "Either path — run evals" block (every `python -m evals run|runs|list|compare|capture-context` line) with `uv run alfred evals calibrate`, `uv run alfred evals list` and `uv run alfred evals run home_control conversation`, plus one line pointing at `docs/evals.md`.
- The spec (its command name already reads `alfred evals`, changed with this plan):
  - Say the `evals` optional extra holds `inspect-ai`, `aiohttp` and `websockets`, with Radicale added in slice 5.
  - Add `ha_state` (fake HA state, an entity's state and attributes) to the checks table.

- [ ] **Step 4: Verify**

Run:
```bash
.venv/bin/python -m pytest tests/evals -q
grep -rnE "evals-runner|alfred-evals (run|list|calibrate)" docs README.md CLAUDE.md evals | grep -v superpowers/plans
```
Expected: the tests pass, including coverage after the PRD edit, and the grep prints nothing.

- [ ] **Step 5: Commit**

```bash
git add -A docs CLAUDE.md evals/coverage.yaml
git commit -m "docs(evals): document the PRD eval suite"
```

---

### Task 16: First real run, triage and the PR

This task runs live against the shared vLLM: at most 4 concurrent proxy requests plus 2 judge connections, and one container. **The controller does it, not a subagent.**

- [ ] **Step 1: Run the full gate once**

```bash
uv run ruff check . && uv run ruff format --check .
.venv/bin/mypy --strict alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
.venv/bin/python -m pytest -q
```
Expected: everything green. Fix anything red before going on.

- [ ] **Step 2: Set up the home-service pin**

```bash
git -C ~/code/alfred-deploy/home-service fetch origin main
git -C ~/code/alfred-deploy/home-service worktree add --detach ~/code/.worktrees/home-service/evals-main origin/main
```

- [ ] **Step 3: Calibrate the judge**

Run: `.venv/bin/alfred evals calibrate`
Expected: one line per category. If a category is under 85%, read the rationales for the items it disagreed on:
- If gemma misreads the rubric, reword the rubric in both the calibration item and the goldens.
- If the label was wrong, flip it, but only with the owner's say-so.

Re-run until the categories are trusted, or record which stay untrusted.

- [ ] **Step 4: Run both suites**

Run: `.venv/bin/alfred evals run home_control conversation --include-pending --home-service ~/code/.worktrees/home-service/evals-main`
Expected:
- The image builds.
- One container boots per suite. Record `boot_seconds`: the spec says a reset path is added if a boot takes more than 60 s, and that goes into the slice 2 plan.
- The scorecard prints and the logs land in `evals/logs/<run>/`.

- [ ] **Step 5: Triage every failure and error**

For each `I`, `N` or `E`, open it in `inspect view --log-dir evals/logs/<run>`, then:
- **E, or a harness bug** (wrong wire format, timing, a check that is wrong): fix it under TDD in the owning module, then re-run that suite.
- **N** (the judge cannot decide): tighten the rubric or the reference.
- **I where Alfred really misbehaved:** keep the golden as it is. That failure is what the suite exists to find. Collect it for Step 7.

Repeat until the run has no `E` and every remaining `I` is a confirmed product behaviour.

- [ ] **Step 6: Push and open the PR**

Push `feat/prd-evals`, then open a PR titled `feat(evals): PRD eval suite slice 1 — real-stack utterance evals on vLLM`. The body has:
- a summary
- the final `report.md` pasted in full
- the boot times
- the calibration agreement
- what slices 2–7 add
- the attribution line from the session's instructions

The owner reviews it. Do not merge.

- [ ] **Step 7: File the product gaps**

The repo is public, so search the open issues first and keep personal data out. For each confirmed misbehaviour, file one GitHub issue:
- the golden id and its failing check reasons
- the observed reply
- a link to the PR

Label each `priority: medium` unless the owner says otherwise. Comment the issue links on the PR.
