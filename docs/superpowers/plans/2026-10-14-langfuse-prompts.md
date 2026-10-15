# Prompts in Langfuse (PR 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** System 2's and the Librarian's prompt prose is served from self-hosted Langfuse, with versions, the `production` label and a link from every model-call span to the prompt version it used. The repo keeps the defaults, which a fresh clone, CI and the evals serve.

**Architecture:**
- A `PromptStore` in `shared/prompts/` fetches text prompts from Langfuse's public API.
  - It caches them in process for `PROMPT_CACHE_TTL_S` and refreshes stale entries in a background task, single-flight per prompt.
  - It keeps the last good copy in Redis.
  - It falls back to that copy, then to the repo defaults under `shared/prompts/defaults/`.
- **System 2.** The engine renders the turn's sections before the run, into `TurnDeps.prompts`. The agent's sync instructions function only orders them around the turn's data.
- **Librarian.** Each pass fetches its rendered prompt into `LibrarianTask.instructions`.
- **Prompt link.** A ContextVar set around each `agent.run` names the prompt versions in use. A `SpanProcessor` copies them onto Pydantic AI's `chat` spans as `langfuse.observation.prompt.name` / `.version`.
- **CLI.** `alfred prompts push|diff|pull` syncs the defaults with Langfuse.
- **Evals.** `alfred evals run --prompt-label <label>` points the eval stack at Langfuse.

**Tech Stack:** Python 3.13, Pydantic AI 2.55.0, httpx (`MockTransport` in tests), OpenTelemetry SDK, redis-py asyncio, fakeredis, typer, Langfuse 4.54 public API (`/api/public/v2/prompts`).

**Spec:** `docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md`:
- component 9, "Prompts in Langfuse";
- delivery-table row 3: `feat/langfuse-prompts`, System 2 and Librarian, verified by "evals with repo defaults and with a Langfuse label".

Base: `master` at `8e5e6e4`. Branch: `feat/langfuse-prompts`. Tracking: #317. The approval classifier's prompt arrives with PR 5 and Reflex's with PR 8 (spec component 9's last two bullets). Neither is in this plan.

## Global Constraints

- **Gate:**
  ```
  set -o pipefail; uv sync -q --all-extras && uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy --strict alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ && PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -p no:randomly
  ```
  Run it once per task, before commit. While iterating, run only the narrow test files.
- **Prompt names, verbatim.** Each name is also its default's file name, `shared/prompts/defaults/<name>.md`.
  - System 2: `system2-personality`, `system2-voice-delivery`, `system2-identity-sir`, `system2-identity-guest`, `system2-location` {area}, `system2-integrations`, `system2-proactivity` {level}.
  - Librarian: `librarian-analyse`, `librarian-extract`, `librarian-compress`, `librarian-resolve` {min_observations, min_days}, `librarian-patterns` {min_occurrences, min_days, tz_name}.
- Variables use Langfuse's `{{name}}` syntax. Code renders the data (current time, recall, routine hint, observations). The store renders only the prose around it.
- **Config, verbatim:** `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `ALFRED_PROMPT_LABEL` (default `production`), `PROMPT_CACHE_TTL_S` (default 60 s). With no `LANGFUSE_HOST`, the store serves repo defaults only.
- The Redis copy's key is `alfred:prompts:{name}:{label}`, verbatim from the spec. Declare it in `shared/streams.py` beside the existing keys.
- Langfuse calls from the store time out after 2 s (`DEFAULT_LANGFUSE_TIMEOUT_S = 2.0`); the CLI uses 10 s.
- No model identifiers in commit messages, code comments or docs prose.
- **The repo is public.** Never write hostnames, IPs, the owner's domain or any Langfuse key into a file. Examples use `http://langfuse-web:3000` (the compose service name) or `http://localhost:3300`.
- Conventional commits, one or more per task. Never push; the controller pushes after its secret-hygiene check.
- **Logging:**
  - `shared/` uses stdlib `logging.getLogger(__name__)` with `%s` arguments.
  - `core/conscious/__main__.py` and `core/librarian/__main__.py`: match the call style already in that file.
- A secret key never appears in a log line, an exception message or a CLI output.

## Rulings (decided while planning)

1. **Defaults path.** The spec's "repo default `prompts/<name>.md`" becomes `shared/prompts/defaults/<name>.md`. The image copies `shared/` already (`Containerfile` line 56) but not a top-level `prompts/`, and every process that reads prompts imports `shared`. If this is wrong, it costs a `git mv` and a new `COPY` line.
2. **Trailing newline.** A default file ends with one `\n`, as editors write it. The loader strips exactly that one, and `pull` writes it back. System 2's personality and voice-delivery sections therefore lose one blank line each against today's prompt. The golden test in Task 5 pins this one intended change and nothing else.
3. **Section prose has no leading newline.** The files hold `## Identity\n…`. `assemble()` adds the `"\n"` separator that today's literals carry.
4. **Render where the data is known.** The spec's `get(name, **variables) -> RenderedPrompt` needs every variable at fetch time. System 2's are known before the run (identity, channel, content type, area, integrations), so the engine renders them into `TurnDeps.prompts`. The agent's instructions function stays sync, and no I/O happens inside the agent graph.
5. **The prompt link is a ContextVar, not baggage**, so it can never leak into outgoing HTTP headers. It goes onto `chat` spans only.
   - `langfuse.observation.prompt.name` / `.version` are set only when the primary prompt came from Langfuse or the Redis copy. A repo default has no Langfuse version to link to.
   - `alfred.prompts` lists every prompt the call used as `name@version` (`name@repo` for a default).
   - System 2's primary prompt is `system2-personality`.
6. **Fallbacks.**
   - **Unseeded prompt.** A 404 from Langfuse is normal before the first `push`. It serves the repo default without a warning.
   - **Langfuse unreachable.** An error, a timeout or a 401 serves the Redis copy, then the repo default. It logs a WARNING on the first failure per prompt and an INFO on recovery.
   - **Cached fallback.** Whatever is served is cached for the TTL, so an outage costs at most one 2 s wait per prompt per process start, never one per turn.
7. **A bad Langfuse edit never breaks a turn.** Two cases serve the repo default with a WARNING naming the prompt and version:
   - a chat-type prompt;
   - a text that uses a variable the code does not pass.

   A caller passing the wrong variables is a programming error and raises `ValueError`.
8. **The Redis copy has no expiry.** Last-known-good has to outlive a long outage.
9. **Keys without a host.** If `LANGFUSE_HOST` is set but either key is missing, the store logs one WARNING at startup and serves repo defaults.
10. **Evals and a loopback-only Langfuse.** The eval container reaches Langfuse through `container_reachable(LANGFUSE_HOST)`. A Langfuse published only on the host's loopback is not reachable from it, and `docs/evals.md` says so: use an address the container can reach. The scorecard's commit line says which prompts it ran.

## Review Focus

1. **Langfuse down or hanging when a process starts.** Each prompt waits at most 2 s once, then serves the Redis copy or the repo default for the TTL, and the turn still answers. → Task 3: `test_an_outage_is_cached_so_turns_do_not_wait`, `test_langfuse_errors_fall_back_to_the_redis_copy`.
2. **First deploy before anyone ran `alfred prompts push`.** Every prompt 404s, and the repo defaults are served with no warning spam. → Task 3: `test_an_unseeded_prompt_serves_the_repo_default_quietly`.
3. **Someone edits a prompt in the Langfuse UI and adds `{{room}}`, or switches it to a chat prompt.** The repo default is served with a WARNING; nothing raises mid-turn. → Task 3: `test_a_langfuse_text_with_an_undeclared_variable_is_refused`, `test_a_chat_prompt_is_refused`.
4. **Several requests arrive at a cold process at once.** There is one Langfuse fetch per prompt, not one per request, and a caller that gives up does not cancel the fetch the others wait on. → Task 3: `test_concurrent_cold_reads_share_one_fetch`, `test_a_cancelled_reader_does_not_cancel_the_shared_fetch`.
5. **Redis is down while Langfuse is up, or both are down.** The Langfuse text is still served; with both down, the repo default is. → Task 3: `test_redis_failures_never_reach_the_caller`.

---

## File map

| Path | Responsibility |
|---|---|
| `shared/prompts/__init__.py` | Package docstring only |
| `shared/prompts/names.py` | Prompt-name constants and each prompt's declared variables |
| `shared/prompts/template.py` | `PromptRef`, `RenderedPrompt`, variable parsing, rendering, repo defaults, `unusable_reason` |
| `shared/prompts/defaults/*.md` | The 12 repo defaults |
| `shared/prompts/langfuse.py` | Async Langfuse prompts client; the `PromptFetcher` / `PromptAdmin` protocols |
| `shared/prompts/store.py` | `PromptStore`: cache, single-flight refresh, Redis copy, fallbacks |
| `shared/prompts/link.py` | `linked_prompts()` ContextVar and `PromptLinkProcessor` |
| `shared/prompts/cli.py` | `alfred prompts push|diff|pull` |
| `core/conscious/system2_prompts.py` | Which sections a turn includes; `System2Prompts`; fetch from store or repo |
| `core/conscious/context_assembler.py` | `assemble()`: sections plus the turn's data, in order |

---

### Task 1: Prompt names, repo defaults and rendering

**Files:**
- Create: `shared/prompts/__init__.py`, `shared/prompts/names.py`, `shared/prompts/template.py`, `shared/prompts/defaults/` (12 `.md` files, generated in Step 1)
- Test: `tests/shared/prompts/__init__.py` (empty), `tests/shared/prompts/test_template.py`

**Interfaces:**
- Produces, in `shared.prompts.names`:
  - the 12 name constants (`SYSTEM2_PERSONALITY`, `SYSTEM2_VOICE_DELIVERY`, `SYSTEM2_IDENTITY_SIR`, `SYSTEM2_IDENTITY_GUEST`, `SYSTEM2_LOCATION`, `SYSTEM2_INTEGRATIONS`, `SYSTEM2_PROACTIVITY`, `LIBRARIAN_ANALYSE`, `LIBRARIAN_EXTRACT`, `LIBRARIAN_COMPRESS`, `LIBRARIAN_RESOLVE`, `LIBRARIAN_PATTERNS`);
  - `PROMPTS: Mapping[str, frozenset[str]]`.
- Produces, in `shared.prompts.template`:
  - `PromptSource = Literal["langfuse", "redis", "repo"]`;
  - `PromptRef(name, version: int | None, source)` with `str()` → `name@3` / `name@repo`;
  - `RenderedPrompt(text, name, version: int | None, source)` with `.ref -> PromptRef`;
  - functions: `variables_in(text) -> frozenset[str]`, `check_variables(name, variables: Mapping[str, object]) -> None` (raises `ValueError`), `render(text, variables) -> str`, `load_default(path) -> str`, `repo_text(name) -> str`, `repo_prompt(name, **variables) -> RenderedPrompt`, `unusable_reason(name, prompt_type: str, text: str | None) -> str | None`;
  - `DEFAULTS_DIR: Path`.

- [ ] **Step 1: Generate the repo defaults from today's sources**

Write this one-off script to the scratchpad (not the repo) and run it from the worktree root with `uv run python <scratchpad>/gen_defaults.py`. It copies the two System 2 files byte for byte and writes the rest from the code that builds them today, so the defaults cannot drift from what production sends.

```python
"""One-off: write shared/prompts/defaults/ from today's prompt sources."""

import shutil
from pathlib import Path

from core.librarian import agents as a

out = Path("shared/prompts/defaults")
out.mkdir(parents=True, exist_ok=True)
src = Path("core/conscious/prompts")
shutil.copyfile(src / "personality.md", out / "system2-personality.md")
shutil.copyfile(src / "voice_delivery.md", out / "system2-voice-delivery.md")
texts = {
    "system2-identity-sir": "## Identity\nYou are speaking with sir (authenticated).",
    "system2-identity-guest": (
        "## Identity\nYou are speaking with a guest. "
        "Do NOT share any personal information about sir."
    ),
    "system2-location": (
        "## Location\nThis request was spoken at the {{area}} satellite. "
        'When a device is referenced without naming a room ("the lights"), '
        "assume the {{area}} area."
    ),
    "system2-integrations": (
        "## Integrations\n"
        "You have integration tools (prefixed `integration_`) for calendar, "
        "weather, health, and finance data. Use them when sir asks about "
        "these topics — they are callable just like other tools."
    ),
    "system2-proactivity": "## Proactivity Level: {{level}}",
    "librarian-analyse": a.ANALYSE_INSTRUCTIONS,
    "librarian-extract": a.EXTRACT_INSTRUCTIONS,
    "librarian-compress": a.COMPRESS_INSTRUCTIONS,
    "librarian-resolve": a.resolve_instructions("{{min_observations}}", "{{min_days}}"),  # type: ignore[arg-type]
    "librarian-patterns": a.pattern_instructions(  # type: ignore[arg-type]
        "{{min_occurrences}}", "{{min_days}}", "{{tz_name}}"
    ),
}
for name, text in texts.items():
    (out / f"{name}.md").write_text(text + "\n", encoding="utf-8")
print(sorted(p.name for p in out.iterdir()))
```

Expected: 12 file names printed. Check that `shared/prompts/defaults/system2-personality.md` is identical to `core/conscious/prompts/personality.md` (`cmp` prints nothing). Leave `core/conscious/prompts/` in place; Task 5 deletes it.

- [ ] **Step 2: Write the failing tests**

`tests/shared/prompts/test_template.py`:

```python
"""Prompt names, the repo defaults and rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.librarian.agents import (
    ANALYSE_INSTRUCTIONS,
    COMPRESS_INSTRUCTIONS,
    EXTRACT_INSTRUCTIONS,
    pattern_instructions,
    resolve_instructions,
)
from shared.prompts.names import (
    LIBRARIAN_ANALYSE,
    LIBRARIAN_COMPRESS,
    LIBRARIAN_EXTRACT,
    LIBRARIAN_PATTERNS,
    LIBRARIAN_RESOLVE,
    PROMPTS,
    SYSTEM2_LOCATION,
    SYSTEM2_PERSONALITY,
)
from shared.prompts.template import (
    DEFAULTS_DIR,
    PromptRef,
    RenderedPrompt,
    check_variables,
    load_default,
    render,
    repo_prompt,
    repo_text,
    unusable_reason,
    variables_in,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_every_prompt_has_a_default_using_exactly_its_variables() -> None:
    for name, declared in PROMPTS.items():
        assert variables_in(repo_text(name)) == declared, name


def test_the_defaults_directory_holds_only_known_prompts() -> None:
    assert {p.stem for p in DEFAULTS_DIR.glob("*.md")} == set(PROMPTS)


def test_load_default_strips_exactly_one_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("line\n\n", encoding="utf-8")
    assert load_default(path) == "line\n"


def test_repo_prompt_renders_every_occurrence() -> None:
    prompt = repo_prompt(SYSTEM2_LOCATION, area="kitchen")
    assert prompt.text.count("kitchen") == 2
    assert "{{" not in prompt.text
    assert (prompt.name, prompt.version, prompt.source) == (SYSTEM2_LOCATION, None, "repo")


def test_variables_tolerate_spaces_inside_the_braces() -> None:
    assert variables_in("a {{ area }} b {{level}}") == frozenset({"area", "level"})
    assert render("at {{ area }}", {"area": "hall"}) == "at hall"


@pytest.mark.parametrize(
    "variables", [{}, {"area": "x", "extra": "y"}, {"room": "x"}], ids=["missing", "extra", "wrong"]
)
def test_check_variables_wants_exactly_the_declared_set(variables: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=SYSTEM2_LOCATION):
        check_variables(SYSTEM2_LOCATION, variables)


def test_an_unknown_prompt_name_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="no-such-prompt"):
        check_variables("no-such-prompt", {})


def test_unusable_reason() -> None:
    assert unusable_reason(SYSTEM2_PERSONALITY, "text", "You are Alfred.") is None
    assert unusable_reason(SYSTEM2_PERSONALITY, "chat", None) == "not a text prompt"
    reason = unusable_reason(SYSTEM2_LOCATION, "text", "at {{area}} in {{room}}")
    assert reason is not None and "room" in reason


def test_refs_name_the_version_or_the_repo() -> None:
    assert str(PromptRef("p", 3, "langfuse")) == "p@3"
    assert str(PromptRef("p", None, "repo")) == "p@repo"
    assert RenderedPrompt("t", "p", 3, "redis").ref == PromptRef("p", 3, "redis")


# Removed in Task 6, when the Librarian's builders are deleted and the defaults become
# the only source.
def test_librarian_defaults_match_todays_builders() -> None:
    assert repo_prompt(LIBRARIAN_ANALYSE).text == ANALYSE_INSTRUCTIONS
    assert repo_prompt(LIBRARIAN_EXTRACT).text == EXTRACT_INSTRUCTIONS
    assert repo_prompt(LIBRARIAN_COMPRESS).text == COMPRESS_INSTRUCTIONS
    resolve = repo_prompt(LIBRARIAN_RESOLVE, min_observations=5, min_days=14)
    assert resolve.text == resolve_instructions(5, 14)
    patterns = repo_prompt(
        LIBRARIAN_PATTERNS, min_occurrences=3, min_days=7, tz_name="America/Los_Angeles"
    )
    assert patterns.text == pattern_instructions(3, 7, "America/Los_Angeles")
```

- [ ] **Step 3: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts/test_template.py -q -p no:randomly`
Expected: collection error, `ModuleNotFoundError: No module named 'shared.prompts'`.

- [ ] **Step 4: Implement**

`shared/prompts/__init__.py`:

```python
"""Prompt prose, served from Langfuse in production with repo defaults (spec component 9)."""
```

`shared/prompts/names.py`:

```python
"""Every prompt the store serves, with the variables its caller passes.

A name is also its repo default's file name (``defaults/<name>.md``) and its name in
Langfuse. A Langfuse version that uses a variable not declared here is refused.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

SYSTEM2_PERSONALITY: Final = "system2-personality"
SYSTEM2_VOICE_DELIVERY: Final = "system2-voice-delivery"
SYSTEM2_IDENTITY_SIR: Final = "system2-identity-sir"
SYSTEM2_IDENTITY_GUEST: Final = "system2-identity-guest"
SYSTEM2_LOCATION: Final = "system2-location"
SYSTEM2_INTEGRATIONS: Final = "system2-integrations"
SYSTEM2_PROACTIVITY: Final = "system2-proactivity"
LIBRARIAN_ANALYSE: Final = "librarian-analyse"
LIBRARIAN_EXTRACT: Final = "librarian-extract"
LIBRARIAN_COMPRESS: Final = "librarian-compress"
LIBRARIAN_RESOLVE: Final = "librarian-resolve"
LIBRARIAN_PATTERNS: Final = "librarian-patterns"

PROMPTS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        SYSTEM2_PERSONALITY: frozenset(),
        SYSTEM2_VOICE_DELIVERY: frozenset(),
        SYSTEM2_IDENTITY_SIR: frozenset(),
        SYSTEM2_IDENTITY_GUEST: frozenset(),
        SYSTEM2_LOCATION: frozenset({"area"}),
        SYSTEM2_INTEGRATIONS: frozenset(),
        SYSTEM2_PROACTIVITY: frozenset({"level"}),
        LIBRARIAN_ANALYSE: frozenset(),
        LIBRARIAN_EXTRACT: frozenset(),
        LIBRARIAN_COMPRESS: frozenset(),
        LIBRARIAN_RESOLVE: frozenset({"min_observations", "min_days"}),
        LIBRARIAN_PATTERNS: frozenset({"min_occurrences", "min_days", "tz_name"}),
    }
)
```

`shared/prompts/template.py`:

```python
"""Prompt text: ``{{variable}}`` rendering, the repo defaults and what a served prompt is."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from shared.prompts.names import PROMPTS

if TYPE_CHECKING:
    from collections.abc import Mapping

PromptSource = Literal["langfuse", "redis", "repo"]

DEFAULTS_DIR: Final = Path(__file__).parent / "defaults"
# Langfuse's variable syntax.
_VARIABLE: Final = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class PromptRef:
    """Which version of a prompt was served; ``version`` is None for a repo default."""

    name: str
    version: int | None
    source: PromptSource

    def __str__(self) -> str:
        return f"{self.name}@{'repo' if self.version is None else self.version}"


@dataclass(frozen=True)
class RenderedPrompt:
    """A prompt's text with its variables filled in, and where it came from."""

    text: str
    name: str
    version: int | None
    source: PromptSource

    @property
    def ref(self) -> PromptRef:
        return PromptRef(self.name, self.version, self.source)


def variables_in(text: str) -> frozenset[str]:
    """The ``{{variable}}`` names *text* uses."""
    return frozenset(_VARIABLE.findall(text))


def check_variables(name: str, variables: Mapping[str, object]) -> None:
    """Raise ``ValueError`` unless *variables* are exactly what prompt *name* declares.

    A mismatch is a bug in the caller, so it fails loudly rather than serving a prompt
    with a hole in it.
    """
    declared = PROMPTS.get(name)
    if declared is None:
        raise ValueError(f"unknown prompt {name!r}")
    if set(variables) != declared:
        raise ValueError(f"prompt {name!r} takes {sorted(declared)}, got {sorted(variables)}")


def render(text: str, variables: Mapping[str, object]) -> str:
    """Fill every ``{{variable}}`` in *text*; the caller has checked the names."""
    return _VARIABLE.sub(lambda m: str(variables[m.group(1)]), text)


def load_default(path: Path) -> str:
    """A default file's text, without the one newline that terminates the file."""
    return path.read_text(encoding="utf-8").removesuffix("\n")


@functools.cache
def repo_text(name: str) -> str:
    """Prompt *name*'s repo default, unrendered."""
    if name not in PROMPTS:
        raise ValueError(f"unknown prompt {name!r}")
    return load_default(DEFAULTS_DIR / f"{name}.md")


def repo_prompt(name: str, **variables: object) -> RenderedPrompt:
    """Prompt *name*'s repo default, rendered."""
    check_variables(name, variables)
    return RenderedPrompt(render(repo_text(name), variables), name, None, "repo")


def unusable_reason(name: str, prompt_type: str, text: str | None) -> str | None:
    """Why a fetched version of prompt *name* cannot be served, or None if it can."""
    if prompt_type != "text" or text is None:
        return "not a text prompt"
    undeclared = variables_in(text) - PROMPTS[name]
    if undeclared:
        return f"uses undeclared variables {sorted(undeclared)}"
    return None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts/test_template.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 6: Gate, then commit**

```bash
git add shared/prompts tests/shared/prompts
git commit -m "feat(prompts): prompt names, repo defaults and {{variable}} rendering"
```

---

### Task 2: Langfuse prompts client

**Files:**
- Create: `shared/prompts/langfuse.py`
- Test: `tests/shared/prompts/test_langfuse.py`

**Interfaces:**
- Produces:
  - `DEFAULT_LANGFUSE_TIMEOUT_S = 2.0`;
  - `LangfusePrompt(name, version: int, type: str, text: str | None, labels: tuple[str, ...])`;
  - `LangfuseError(RuntimeError)`.
- Protocols:
  - `PromptFetcher`: `async get(name, *, label) -> LangfusePrompt | None`, `async aclose()`.
  - `PromptAdmin(PromptFetcher)`: `async create(name, text, *, labels: Sequence[str], commit_message: str) -> LangfusePrompt`, `async set_labels(name, version, labels: Sequence[str]) -> LangfusePrompt`.
- Class `LangfuseClient(host, *, public_key, secret_key, timeout_s=DEFAULT_LANGFUSE_TIMEOUT_S, transport=None)` implements `PromptAdmin`.
- Every failure other than a GET 404 raises `LangfuseError`; a GET 404 returns None.

- [ ] **Step 1: Write the failing tests**

`tests/shared/prompts/test_langfuse.py`:

```python
"""The Langfuse prompts client: auth, paths, the 404 rule and error mapping."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from shared.prompts.langfuse import LangfuseClient, LangfuseError, LangfusePrompt


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "system2-personality",
        "version": 4,
        "type": "text",
        "prompt": "You are Alfred.",
        "labels": ["production", "latest"],
        "config": {},
    }
    body.update(overrides)
    return body


def _client(handler: Any) -> LangfuseClient:
    return LangfuseClient(
        "http://langfuse.test/",
        public_key="pk-test",
        secret_key="sk-test",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_get_sends_basic_auth_and_the_label_and_parses_a_text_prompt() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_body())

    prompt = await _client(handler).get("system2-personality", label="production")

    assert prompt == LangfusePrompt(
        "system2-personality", 4, "text", "You are Alfred.", ("production", "latest")
    )
    (request,) = seen
    assert request.url.path == "/api/public/v2/prompts/system2-personality"
    assert request.url.params["label"] == "production"
    expected = base64.b64encode(b"pk-test:sk-test").decode()
    assert request.headers["authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
async def test_get_returns_none_for_an_unseeded_prompt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Prompt not found", "error": "NotFound"})

    assert await _client(handler).get("system2-personality", label="production") is None


@pytest.mark.asyncio
async def test_a_chat_prompt_has_no_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(type="chat", prompt=[{"role": "system"}]))

    prompt = await _client(handler).get("system2-personality", label="production")
    assert prompt is not None and (prompt.type, prompt.text) == ("chat", None)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"message": "Unauthorized"}),
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"name": "x"}),
    ],
    ids=["401", "500", "not-json", "wrong-shape"],
)
@pytest.mark.asyncio
async def test_failures_are_langfuse_errors(response: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    with pytest.raises(LangfuseError):
        await _client(handler).get("system2-personality", label="production")


@pytest.mark.asyncio
async def test_a_transport_error_is_a_langfuse_error_without_the_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LangfuseError) as caught:
        await _client(handler).get("system2-personality", label="production")
    assert "sk-test" not in str(caught.value)


@pytest.mark.asyncio
async def test_create_posts_a_text_prompt_with_labels_and_a_commit_message() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/api/public/v2/prompts")
        seen.append(json.loads(request.content))
        return httpx.Response(201, json=_body(version=5, labels=["latest"]))

    created = await _client(handler).create(
        "system2-personality", "You are Alfred.", labels=[], commit_message="alfred prompts push"
    )

    assert created.version == 5
    assert seen == [
        {
            "type": "text",
            "name": "system2-personality",
            "prompt": "You are Alfred.",
            "labels": [],
            "config": {},
            "commitMessage": "alfred prompts push",
        }
    ]


@pytest.mark.asyncio
async def test_set_labels_patches_the_version() -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=_body(labels=["production", "latest"]))

    await _client(handler).set_labels("system2-personality", 4, ["production"])

    assert seen == [
        (
            "PATCH",
            "/api/public/v2/prompts/system2-personality/versions/4",
            {"newLabels": ["production"]},
        )
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts/test_langfuse.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'shared.prompts.langfuse'`.

- [ ] **Step 3: Implement**

`shared/prompts/langfuse.py`:

```python
"""Langfuse's prompts API, the three calls Alfred makes: read a label, create a version,
move labels.

Basic auth with the project's public and secret keys. Every failure is a
``LangfuseError`` except a read of a prompt that does not exist, which is None: an
unseeded prompt is normal before the first ``alfred prompts push``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DEFAULT_LANGFUSE_TIMEOUT_S: Final = 2.0
_PROMPTS: Final = "/api/public/v2/prompts"


class LangfuseError(RuntimeError):
    """Langfuse could not be reached, refused the request or answered nonsense."""


@dataclass(frozen=True)
class LangfusePrompt:
    """One prompt version; ``text`` is None for a chat prompt."""

    name: str
    version: int
    type: str
    text: str | None
    labels: tuple[str, ...]


class PromptFetcher(Protocol):
    async def get(self, name: str, *, label: str) -> LangfusePrompt | None: ...

    async def aclose(self) -> None: ...


class PromptAdmin(PromptFetcher, Protocol):
    async def create(
        self, name: str, text: str, *, labels: Sequence[str], commit_message: str
    ) -> LangfusePrompt: ...

    async def set_labels(
        self, name: str, version: int, labels: Sequence[str]
    ) -> LangfusePrompt: ...


class LangfuseClient:
    """Async client for Langfuse's public prompts API."""

    def __init__(
        self,
        host: str,
        *,
        public_key: str,
        secret_key: str,
        timeout_s: float = DEFAULT_LANGFUSE_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=host.rstrip("/"),
            auth=(public_key, secret_key),
            timeout=timeout_s,
            transport=transport,
        )

    async def get(self, name: str, *, label: str) -> LangfusePrompt | None:
        """The version of *name* carrying *label*, or None if there is none."""
        response = await self._request("GET", _path(name), params={"label": label})
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        return _prompt(_json(response))

    async def create(
        self, name: str, text: str, *, labels: Sequence[str], commit_message: str
    ) -> LangfusePrompt:
        """A new text version of *name*; Langfuse adds the ``latest`` label itself."""
        body = {
            "type": "text",
            "name": name,
            "prompt": text,
            "labels": list(labels),
            "config": {},
            "commitMessage": commit_message,
        }
        return _prompt(_json(await self._request("POST", _PROMPTS, json=body)))

    async def set_labels(self, name: str, version: int, labels: Sequence[str]) -> LangfusePrompt:
        """Put *labels* on *version*; Langfuse moves each label off any other version."""
        path = f"{_path(name)}/versions/{version}"
        body = {"newLabels": list(labels)}
        return _prompt(_json(await self._request("PATCH", path, json=body)))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            raise LangfuseError(f"{method} {path}: {type(exc).__name__}: {exc}") from exc
        if method == "GET" and response.status_code == httpx.codes.NOT_FOUND:
            return response
        if response.is_error:
            raise LangfuseError(f"{method} {path}: HTTP {response.status_code}")
        return response


def _path(name: str) -> str:
    return f"{_PROMPTS}/{quote(name, safe='')}"


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise LangfuseError(f"Langfuse answered HTTP {response.status_code} without JSON") from exc


def _prompt(data: Any) -> LangfusePrompt:
    try:
        prompt = data["prompt"]
        return LangfusePrompt(
            name=str(data["name"]),
            version=int(data["version"]),
            type=str(data["type"]),
            text=prompt if isinstance(prompt, str) else None,
            labels=tuple(str(label) for label in data.get("labels", [])),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise LangfuseError(f"unexpected prompt shape: {exc!r}") from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts/test_langfuse.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Gate, then commit**

```bash
git add shared/prompts/langfuse.py tests/shared/prompts/test_langfuse.py
git commit -m "feat(prompts): async client for Langfuse's prompts API"
```

---

### Task 3: PromptStore and its config

**Files:**
- Create: `shared/prompts/store.py`, `tests/shared/prompts/fakes.py`, `tests/shared/prompts/test_store.py`, `tests/shared/test_config_prompts.py`
- Modify: `shared/streams.py` (add the key), `shared/config.py` (constants, `AlfredConfig` fields at ~line 423, `from_env` at ~line 532), `.env.example`

**Interfaces:**
- Consumes:
  - Task 1: `RenderedPrompt`, `check_variables`, `render`, `repo_text`, `unusable_reason`, `PromptSource`.
  - Task 2: `PromptFetcher`, `LangfuseClient`, `LangfuseError`, `LangfusePrompt`.
- Produces:
  - In `shared.config`: `DEFAULT_PROMPT_LABEL = "production"`, `DEFAULT_PROMPT_CACHE_TTL_S = 60.0`, and the `AlfredConfig` fields `langfuse_host: str`, `langfuse_public_key: str`, `langfuse_secret_key: str`, `prompt_label: str`, `prompt_cache_ttl_s: float`.
  - In `shared.streams`: `PROMPT_COPY_KEY = "alfred:prompts:{name}:{label}"`.
  - `PromptStore`:
    - `PromptStore(*, langfuse: PromptFetcher | None = None, redis: AioRedis | None = None, label=DEFAULT_PROMPT_LABEL, ttl_s=DEFAULT_PROMPT_CACHE_TTL_S, clock=time.monotonic)`;
    - `PromptStore.from_config(config, *, redis) -> PromptStore`;
    - `async get(name, **variables) -> RenderedPrompt`;
    - `describe() -> str`;
    - `async aclose()`.

- [ ] **Step 1: Add the config, key and fake**

In `shared/config.py`, beside the other `DEFAULT_*` constants near the top:

```python
DEFAULT_PROMPT_LABEL: Final = "production"
DEFAULT_PROMPT_CACHE_TTL_S: Final = 60.0
```

(Use `Final` if the file already imports it; otherwise match how the neighbouring defaults are declared.)

In `AlfredConfig`, after `proactivity_level`:

```python
    # Prompts (shared/prompts): served from Langfuse when LANGFUSE_HOST is set, else
    # the repo defaults.
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    prompt_label: str = DEFAULT_PROMPT_LABEL
    prompt_cache_ttl_s: float = DEFAULT_PROMPT_CACHE_TTL_S
```

In `from_env`, after `proactivity_level=...`:

```python
            langfuse_host=os.getenv("LANGFUSE_HOST", "").strip(),
            langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
            langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
            prompt_label=os.getenv("ALFRED_PROMPT_LABEL", "").strip() or DEFAULT_PROMPT_LABEL,
            prompt_cache_ttl_s=positive_seconds_env(
                "PROMPT_CACHE_TTL_S", DEFAULT_PROMPT_CACHE_TTL_S
            ),
```

In `shared/streams.py`, beside the other keys:

```python
# Last prompt text fetched from Langfuse, per prompt and label (shared.prompts.store).
# A format string: PROMPT_COPY_KEY.format(name=..., label=...).
PROMPT_COPY_KEY = "alfred:prompts:{name}:{label}"
```

In `.env.example`, add a section (blank values, as the file does for other secrets):

```
# --- Prompts (optional) ---
# With LANGFUSE_HOST set, System 2's and the Librarian's prompts are served from Langfuse
# at ALFRED_PROMPT_LABEL; without it, from the repo defaults in shared/prompts/defaults/.
# From the alfred container: http://langfuse-web:3000 (the compose service name).
LANGFUSE_HOST=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
ALFRED_PROMPT_LABEL=production
PROMPT_CACHE_TTL_S=60
```

`tests/shared/prompts/fakes.py`:

```python
"""An in-memory stand-in for Langfuse's prompts API."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from shared.prompts.langfuse import LangfusePrompt


class FakeLangfuse:
    """Serves *texts* by name at version *version*; a missing name is unseeded (None).

    ``error`` makes every read raise it. ``gate`` (when set) holds every read until it is
    set. ``created`` / ``labelled`` record the admin calls.
    """

    def __init__(
        self,
        texts: dict[str, str] | None = None,
        *,
        version: int = 7,
        prompt_type: str = "text",
        labels: tuple[str, ...] = ("production", "latest"),
    ) -> None:
        self.texts = dict(texts or {})
        self.version = version
        self.prompt_type = prompt_type
        self.labels = labels
        self.error: Exception | None = None
        self.gate: asyncio.Event | None = None
        self.calls: list[tuple[str, str]] = []
        self.created: list[tuple[str, str, list[str]]] = []
        self.labelled: list[tuple[str, int, list[str]]] = []
        self.closed = False

    async def get(self, name: str, *, label: str) -> LangfusePrompt | None:
        self.calls.append((name, label))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        text = self.texts.get(name)
        if text is None:
            return None
        chat = self.prompt_type != "text"
        return LangfusePrompt(
            name, self.version, self.prompt_type, None if chat else text, self.labels
        )

    async def create(
        self, name: str, text: str, *, labels: Sequence[str], commit_message: str
    ) -> LangfusePrompt:
        self.version += 1
        self.texts[name] = text
        self.created.append((name, text, list(labels)))
        return LangfusePrompt(name, self.version, "text", text, (*labels, "latest"))

    async def set_labels(self, name: str, version: int, labels: Sequence[str]) -> LangfusePrompt:
        self.labelled.append((name, version, list(labels)))
        return LangfusePrompt(name, version, "text", self.texts[name], (*labels, "latest"))

    async def aclose(self) -> None:
        self.closed = True
```

- [ ] **Step 2: Write the failing tests**

`tests/shared/prompts/test_store.py`:

```python
"""PromptStore: Langfuse first, then the Redis copy, then the repo default."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from shared.prompts.langfuse import LangfuseError
from shared.prompts.names import SYSTEM2_LOCATION, SYSTEM2_PERSONALITY
from shared.prompts.store import PromptStore
from shared.prompts.template import repo_text
from shared.streams import PROMPT_COPY_KEY
from tests.shared.prompts.fakes import FakeLangfuse


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _store(fake: FakeLangfuse, **kwargs: object) -> tuple[PromptStore, Clock]:
    clock = Clock()
    return PromptStore(langfuse=fake, clock=clock, **kwargs), clock  # type: ignore[arg-type]


async def _settle() -> None:
    """Let background refreshes on the in-memory fake finish."""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_without_langfuse_the_repo_default_is_served() -> None:
    prompt = await PromptStore().get(SYSTEM2_LOCATION, area="hall")
    assert (prompt.source, prompt.version) == ("repo", None)
    assert "hall satellite" in prompt.text


@pytest.mark.asyncio
async def test_langfuse_text_is_rendered_at_the_stores_label() -> None:
    fake = FakeLangfuse({SYSTEM2_LOCATION: "In the {{area}}."})
    store, _ = _store(fake, label="candidate")

    prompt = await store.get(SYSTEM2_LOCATION, area="hall")

    assert (prompt.text, prompt.version, prompt.source) == ("In the hall.", 7, "langfuse")
    assert fake.calls == [(SYSTEM2_LOCATION, "candidate")]


@pytest.mark.asyncio
async def test_reads_within_the_ttl_are_served_from_memory() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "You are Alfred."})
    store, clock = _store(fake)

    await store.get(SYSTEM2_PERSONALITY)
    clock.now += 59
    await store.get(SYSTEM2_PERSONALITY)

    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_a_stale_read_serves_the_cached_text_and_refreshes_in_the_background() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "v1"})
    store, clock = _store(fake)
    await store.get(SYSTEM2_PERSONALITY)

    fake.texts[SYSTEM2_PERSONALITY] = "v2"
    fake.gate = asyncio.Event()
    clock.now += 61
    stale = await asyncio.wait_for(store.get(SYSTEM2_PERSONALITY), timeout=0.5)
    assert stale.text == "v1"

    fake.gate.set()
    await _settle()
    assert (await store.get(SYSTEM2_PERSONALITY)).text == "v2"
    await store.aclose()


@pytest.mark.asyncio
async def test_an_unseeded_prompt_serves_the_repo_default_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, _ = _store(FakeLangfuse())
    with caplog.at_level(logging.INFO, logger="shared.prompts.store"):
        prompt = await store.get(SYSTEM2_PERSONALITY)
    assert (prompt.text, prompt.source) == (repo_text(SYSTEM2_PERSONALITY), "repo")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_langfuse_errors_fall_back_to_the_redis_copy() -> None:
    redis = fakeredis.FakeAsyncRedis()
    first, _ = _store(FakeLangfuse({SYSTEM2_PERSONALITY: "From Langfuse."}), redis=redis)
    await first.get(SYSTEM2_PERSONALITY)  # writes the copy

    down = FakeLangfuse()
    down.error = LangfuseError("refused")
    second, _ = _store(down, redis=redis)
    prompt = await second.get(SYSTEM2_PERSONALITY)

    assert (prompt.text, prompt.version, prompt.source) == ("From Langfuse.", 7, "redis")
    raw = await redis.get(PROMPT_COPY_KEY.format(name=SYSTEM2_PERSONALITY, label="production"))
    assert json.loads(raw) == {"text": "From Langfuse.", "version": 7}


@pytest.mark.asyncio
async def test_an_outage_is_cached_so_turns_do_not_wait(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeLangfuse()
    fake.error = LangfuseError("timed out")
    store, clock = _store(fake)

    with caplog.at_level(logging.WARNING, logger="shared.prompts.store"):
        first = await store.get(SYSTEM2_PERSONALITY)
        await store.get(SYSTEM2_PERSONALITY)
        clock.now += 61
        await store.get(SYSTEM2_PERSONALITY)
        await _settle()

    assert first.source == "repo"
    assert len(fake.calls) == 2  # the cold read and one background refresh, not one per read
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    await store.aclose()


@pytest.mark.asyncio
async def test_a_langfuse_text_with_an_undeclared_variable_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, _ = _store(FakeLangfuse({SYSTEM2_LOCATION: "In the {{room}}."}))
    with caplog.at_level(logging.WARNING, logger="shared.prompts.store"):
        prompt = await store.get(SYSTEM2_LOCATION, area="hall")
    assert prompt.source == "repo"
    assert any("room" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_chat_prompt_is_refused(caplog: pytest.LogCaptureFixture) -> None:
    store, _ = _store(FakeLangfuse({SYSTEM2_PERSONALITY: "x"}, prompt_type="chat"))
    with caplog.at_level(logging.WARNING, logger="shared.prompts.store"):
        prompt = await store.get(SYSTEM2_PERSONALITY)
    assert prompt.source == "repo"
    assert any("not a text prompt" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_concurrent_cold_reads_share_one_fetch() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "Shared."})
    fake.gate = asyncio.Event()
    store, _ = _store(fake)

    readers = [asyncio.create_task(store.get(SYSTEM2_PERSONALITY)) for _ in range(5)]
    await _settle()
    fake.gate.set()
    results = await asyncio.gather(*readers)

    assert {r.text for r in results} == {"Shared."}
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_a_cancelled_reader_does_not_cancel_the_shared_fetch() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "Shared."})
    fake.gate = asyncio.Event()
    store, _ = _store(fake)

    quitter = asyncio.create_task(store.get(SYSTEM2_PERSONALITY))
    stayer = asyncio.create_task(store.get(SYSTEM2_PERSONALITY))
    await _settle()
    quitter.cancel()
    fake.gate.set()

    assert (await stayer).text == "Shared."
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_redis_failures_never_reach_the_caller() -> None:
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=RedisConnectionError("down"))
    redis.set = AsyncMock(side_effect=RedisConnectionError("down"))

    up, _ = _store(FakeLangfuse({SYSTEM2_PERSONALITY: "Up."}), redis=redis)
    assert (await up.get(SYSTEM2_PERSONALITY)).text == "Up."

    down = FakeLangfuse()
    down.error = LangfuseError("refused")
    both_down, _ = _store(down, redis=redis)
    assert (await both_down.get(SYSTEM2_PERSONALITY)).source == "repo"


@pytest.mark.asyncio
async def test_wrong_variables_are_a_value_error_before_any_fetch() -> None:
    fake = FakeLangfuse({SYSTEM2_LOCATION: "x"})
    store, _ = _store(fake)
    with pytest.raises(ValueError, match=SYSTEM2_LOCATION):
        await store.get(SYSTEM2_LOCATION)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_aclose_cancels_refreshes_and_closes_the_client() -> None:
    fake = FakeLangfuse({SYSTEM2_PERSONALITY: "v1"})
    store, clock = _store(fake)
    await store.get(SYSTEM2_PERSONALITY)
    fake.gate = asyncio.Event()
    clock.now += 61
    await store.get(SYSTEM2_PERSONALITY)  # starts a refresh that never finishes

    await asyncio.wait_for(store.aclose(), timeout=0.5)

    assert fake.closed


def test_describe_names_the_source() -> None:
    assert PromptStore().describe() == "repo defaults"
    store, _ = _store(FakeLangfuse(), label="candidate")
    assert store.describe() == "Langfuse, label candidate"
```

`tests/shared/test_config_prompts.py`:

```python
"""Prompt settings: env parsing and how the store is built from them."""

from __future__ import annotations

import logging

import pytest

from shared.config import DEFAULT_PROMPT_CACHE_TTL_S, AlfredConfig
from shared.prompts.store import PromptStore


def test_defaults_without_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "ALFRED_PROMPT_LABEL",
        "PROMPT_CACHE_TTL_S",
    ):
        monkeypatch.delenv(name, raising=False)
    config = AlfredConfig.from_env()
    assert (config.langfuse_host, config.prompt_label) == ("", "production")
    assert config.prompt_cache_ttl_s == DEFAULT_PROMPT_CACHE_TTL_S
    assert PromptStore.from_config(config, redis=None).describe() == "repo defaults"


def test_langfuse_settings_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse-web:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("ALFRED_PROMPT_LABEL", "candidate")
    monkeypatch.setenv("PROMPT_CACHE_TTL_S", "5")
    config = AlfredConfig.from_env()
    assert config.prompt_cache_ttl_s == 5.0
    assert PromptStore.from_config(config, redis=None).describe() == "Langfuse, label candidate"


def test_a_host_without_keys_serves_repo_defaults_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse-web:3000")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    with caplog.at_level(logging.WARNING, logger="shared.prompts.store"):
        store = PromptStore.from_config(AlfredConfig.from_env(), redis=None)
    assert store.describe() == "repo defaults"
    assert any("LANGFUSE_PUBLIC_KEY" in r.getMessage() for r in caplog.records)


def test_a_bad_ttl_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPT_CACHE_TTL_S", "soon")
    with pytest.raises(RuntimeError, match="PROMPT_CACHE_TTL_S"):
        AlfredConfig.from_env()
```

- [ ] **Step 3: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts/test_store.py tests/shared/test_config_prompts.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'shared.prompts.store'`.

- [ ] **Step 4: Implement**

`shared/prompts/store.py`:

```python
"""Where prompt text comes from: Langfuse, then its last copy in Redis, then the repo.

Reads are served from memory. An entry older than the TTL is still served while one
background task per prompt refreshes it, so a turn never waits on Langfuse once a
process has warmed up. A cold read waits for the fetch, which a 2 s client timeout
bounds, and whatever it ends up serving (a fallback included) is cached for the TTL, so
an outage costs one wait per prompt, not one per turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shared.config import DEFAULT_PROMPT_CACHE_TTL_S, DEFAULT_PROMPT_LABEL
from shared.prompts.langfuse import LangfuseClient, LangfuseError
from shared.prompts.template import (
    PromptSource,
    RenderedPrompt,
    check_variables,
    render,
    repo_text,
    unusable_reason,
)
from shared.streams import PROMPT_COPY_KEY

if TYPE_CHECKING:
    from collections.abc import Callable

    from shared.config import AlfredConfig
    from shared.prompts.langfuse import PromptFetcher
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Template:
    text: str
    version: int | None
    source: PromptSource


@dataclass(frozen=True)
class _Entry:
    template: _Template
    fetched_at: float


def _repo(name: str) -> _Template:
    return _Template(repo_text(name), None, "repo")


class PromptStore:
    """Serves rendered prompts; with no Langfuse client, the repo defaults only."""

    def __init__(
        self,
        *,
        langfuse: PromptFetcher | None = None,
        redis: AioRedis | None = None,
        label: str = DEFAULT_PROMPT_LABEL,
        ttl_s: float = DEFAULT_PROMPT_CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._langfuse = langfuse
        self._redis = redis
        self._label = label
        self._ttl_s = ttl_s
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self._inflight: dict[str, asyncio.Task[_Template]] = {}
        self._failing: set[str] = set()

    @classmethod
    def from_config(cls, config: AlfredConfig, *, redis: AioRedis | None) -> PromptStore:
        """Langfuse-backed when ``LANGFUSE_HOST`` and both keys are set, else repo-only."""
        if not config.langfuse_host:
            return cls()
        missing = [
            name
            for name, value in (
                ("LANGFUSE_PUBLIC_KEY", config.langfuse_public_key),
                ("LANGFUSE_SECRET_KEY", config.langfuse_secret_key),
            )
            if not value
        ]
        if missing:
            logger.warning(
                "LANGFUSE_HOST is set but %s is not; serving the repo prompts",
                " and ".join(missing),
            )
            return cls()
        client = LangfuseClient(
            config.langfuse_host,
            public_key=config.langfuse_public_key,
            secret_key=config.langfuse_secret_key,
        )
        return cls(
            langfuse=client, redis=redis, label=config.prompt_label, ttl_s=config.prompt_cache_ttl_s
        )

    def describe(self) -> str:
        """Where prompts come from, for the startup log."""
        if self._langfuse is None:
            return "repo defaults"
        return f"Langfuse, label {self._label}"

    async def get(self, name: str, **variables: object) -> RenderedPrompt:
        """Prompt *name* rendered with *variables*, which must be exactly its declared set.

        Raises:
            ValueError: *name* is unknown or *variables* are not its declared set.
        """
        check_variables(name, variables)
        template = await self._template(name)
        return RenderedPrompt(
            render(template.text, variables), name, template.version, template.source
        )

    async def aclose(self) -> None:
        """Cancel in-flight refreshes and close the Langfuse client."""
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._langfuse is not None:
            await self._langfuse.aclose()

    async def _template(self, name: str) -> _Template:
        if self._langfuse is None:
            return _repo(name)
        entry = self._cache.get(name)
        if entry is None:
            # Shielded: a reader that gives up must not cancel the fetch others await.
            return await asyncio.shield(self._refresh(name))
        if self._clock() - entry.fetched_at >= self._ttl_s:
            self._refresh(name)
        return entry.template

    def _refresh(self, name: str) -> asyncio.Task[_Template]:
        """The one fetch in flight for *name*, started if there is none."""
        task = self._inflight.get(name)
        if task is None:
            task = asyncio.create_task(self._fetch(name), name=f"prompt-refresh:{name}")
            self._inflight[name] = task
            task.add_done_callback(lambda _: self._inflight.pop(name, None))
        return task

    async def _fetch(self, name: str) -> _Template:
        assert self._langfuse is not None
        try:
            remote = await self._langfuse.get(name, label=self._label)
        except LangfuseError as exc:
            self._note_failure(name, exc)
            template = await self._copy(name) or _repo(name)
        else:
            self._note_recovery(name)
            if remote is None:
                template = _repo(name)  # not seeded yet: normal before the first push
            elif (reason := unusable_reason(name, remote.type, remote.text)) is not None:
                logger.warning(
                    "Langfuse prompt %s v%d %s; serving the repo default",
                    name,
                    remote.version,
                    reason,
                )
                template = _repo(name)
            else:
                assert remote.text is not None
                template = _Template(remote.text, remote.version, "langfuse")
                await self._save_copy(name, template)
        self._cache[name] = _Entry(template, self._clock())
        return template

    def _key(self, name: str) -> str:
        return PROMPT_COPY_KEY.format(name=name, label=self._label)

    async def _copy(self, name: str) -> _Template | None:
        """The last Langfuse text fetched for *name*, if Redis has a usable one."""
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._key(name))
            if raw is None:
                return None
            data = json.loads(raw)
            text, version = data["text"], data["version"]
        except Exception as exc:  # noqa: BLE001 — a fallback must never raise
            logger.debug("No usable Redis copy of prompt %s: %r", name, exc)
            return None
        if not isinstance(text, str) or not isinstance(version, int):
            return None
        if unusable_reason(name, "text", text) is not None:
            return None
        return _Template(text, version, "redis")

    async def _save_copy(self, name: str, template: _Template) -> None:
        if self._redis is None:
            return
        payload = json.dumps({"text": template.text, "version": template.version})
        with contextlib.suppress(Exception):
            await self._redis.set(self._key(name), payload)

    def _note_failure(self, name: str, exc: LangfuseError) -> None:
        if name in self._failing:
            logger.debug("Langfuse still unreachable for prompt %s: %s", name, exc)
            return
        self._failing.add(name)
        logger.warning(
            "Langfuse unreachable for prompt %s (%s); serving the last copy or the repo default",
            name,
            exc,
        )

    def _note_recovery(self, name: str) -> None:
        if name in self._failing:
            self._failing.discard(name)
            logger.info("Langfuse reachable again for prompt %s", name)
```

If ruff flags `contextlib.suppress(Exception)` (S110/BLE001), replace it with an explicit `try/except Exception as exc: logger.debug("Could not save the Redis copy of prompt %s: %r", name, exc)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts tests/shared/test_config_prompts.py tests/shared/test_env_example.py -q -p no:randomly`
Expected: all pass. If `test_env_example.py` fails, it names a variable `.env.example` is missing; add it there.

- [ ] **Step 6: Gate, then commit**

```bash
git add shared/prompts/store.py shared/streams.py shared/config.py .env.example tests/shared/prompts tests/shared/test_config_prompts.py
git commit -m "feat(prompts): PromptStore with lazy refresh, a Redis copy and repo fallbacks"
```

---

### Task 4: Link model-call spans to prompt versions

**Files:**
- Create: `shared/prompts/link.py`, `tests/shared/prompts/test_link.py`
- Modify: `shared/otel.py:init_tracing` (register the processor next to `LangfuseBaggageProcessor`)

**Interfaces:**
- Consumes: Task 1's `PromptRef`.
- Produces:
  - constants `PROMPT_NAME_ATTR = "langfuse.observation.prompt.name"`, `PROMPT_VERSION_ATTR = "langfuse.observation.prompt.version"`, `PROMPTS_ATTR = "alfred.prompts"`;
  - context manager `linked_prompts(primary: PromptRef, *others: PromptRef) -> Iterator[None]`;
  - span processor `PromptLinkProcessor`.

- [ ] **Step 1: Write the failing tests**

`tests/shared/prompts/test_link.py`. The first test runs a real Pydantic AI agent, which is what makes it the spec's "verify how to attach them to Pydantic AI's `chat` spans".

```python
"""Model-call spans carry the prompt versions the call used."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.capabilities.instrumentation import Instrumentation
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings

from shared.prompts.link import (
    PROMPT_NAME_ATTR,
    PROMPT_VERSION_ATTR,
    PROMPTS_ATTR,
    PromptLinkProcessor,
    linked_prompts,
)
from shared.prompts.template import PromptRef


def _hi(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("hi")])


def _traced_agent() -> tuple[Agent[None, str], InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(PromptLinkProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    agent: Agent[None, str] = Agent(
        FunctionModel(_hi),
        instructions="x",
        capabilities=[
            Instrumentation(settings=InstrumentationSettings(tracer_provider=provider))
        ],
    )
    return agent, exporter


@pytest.mark.asyncio
async def test_chat_spans_carry_the_primary_prompt_and_every_version() -> None:
    agent, exporter = _traced_agent()
    primary = PromptRef("system2-personality", 4, "langfuse")
    other = PromptRef("system2-identity-sir", None, "repo")

    with linked_prompts(primary, other):
        await agent.run("q")

    spans = exporter.get_finished_spans()
    (chat,) = [s for s in spans if s.name.startswith("chat")]
    attrs = chat.attributes or {}
    assert attrs[PROMPT_NAME_ATTR] == "system2-personality"
    assert attrs[PROMPT_VERSION_ATTR] == 4
    assert attrs[PROMPTS_ATTR] == ("system2-personality@4", "system2-identity-sir@repo")
    for span in spans:
        if not span.name.startswith("chat"):
            assert PROMPT_NAME_ATTR not in (span.attributes or {})


@pytest.mark.asyncio
async def test_a_repo_default_is_listed_but_not_linked() -> None:
    agent, exporter = _traced_agent()
    with linked_prompts(PromptRef("librarian-analyse", None, "repo")):
        await agent.run("q")
    (chat,) = [s for s in exporter.get_finished_spans() if s.name.startswith("chat")]
    attrs = chat.attributes or {}
    assert PROMPT_NAME_ATTR not in attrs
    assert attrs[PROMPTS_ATTR] == ("librarian-analyse@repo",)


@pytest.mark.asyncio
async def test_a_redis_copy_links_to_its_langfuse_version() -> None:
    agent, exporter = _traced_agent()
    with linked_prompts(PromptRef("librarian-analyse", 2, "redis")):
        await agent.run("q")
    (chat,) = [s for s in exporter.get_finished_spans() if s.name.startswith("chat")]
    assert (chat.attributes or {})[PROMPT_VERSION_ATTR] == 2


@pytest.mark.asyncio
async def test_nothing_is_set_outside_a_link() -> None:
    agent, exporter = _traced_agent()
    await agent.run("q")
    for span in exporter.get_finished_spans():
        assert PROMPTS_ATTR not in (span.attributes or {})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts/test_link.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'shared.prompts.link'`.

- [ ] **Step 3: Implement**

`shared/prompts/link.py`:

```python
"""Which prompt versions a model call used, on its span, so Langfuse links the two.

The caller wraps ``agent.run`` in ``linked_prompts``. Pydantic AI sets
``gen_ai.operation.name`` when it starts a span, so the processor can tell a model call
(``chat``) from the agent run around it. A ContextVar rather than baggage: baggage
is propagated in outgoing HTTP headers, and prompt names have no business there.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from opentelemetry.sdk.trace import SpanProcessor

if TYPE_CHECKING:
    from collections.abc import Iterator

    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import Span

    from shared.prompts.template import PromptRef

PROMPT_NAME_ATTR: Final = "langfuse.observation.prompt.name"
PROMPT_VERSION_ATTR: Final = "langfuse.observation.prompt.version"
PROMPTS_ATTR: Final = "alfred.prompts"
_OPERATION: Final = "gen_ai.operation.name"
_CHAT: Final = "chat"


@dataclass(frozen=True)
class _Link:
    primary: PromptRef
    others: tuple[PromptRef, ...]


_LINK: ContextVar[_Link | None] = ContextVar("alfred_prompt_link", default=None)


@contextmanager
def linked_prompts(primary: PromptRef, *others: PromptRef) -> Iterator[None]:
    """Model calls inside link to *primary* and list every prompt they used."""
    token = _LINK.set(_Link(primary, others))
    try:
        yield
    finally:
        _LINK.reset(token)


class PromptLinkProcessor(SpanProcessor):
    """Copies the current prompt link onto each model-call span as it starts."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        link = _LINK.get()
        if link is None or (span.attributes or {}).get(_OPERATION) != _CHAT:
            return
        primary = link.primary
        # A repo default has no Langfuse version to link to; a Redis copy has one.
        if primary.source != "repo" and primary.version is not None:
            span.set_attribute(PROMPT_NAME_ATTR, primary.name)
            span.set_attribute(PROMPT_VERSION_ATTR, primary.version)
        span.set_attribute(PROMPTS_ATTR, [str(ref) for ref in (primary, *link.others)])
```

Mirror `LangfuseBaggageProcessor`'s `on_start` signature and imports exactly if they differ from the above (mypy decides).

In `shared/otel.py:init_tracing`, replace

```python
    # First, so the attributes are set before any exporter sees the span.
    provider.add_span_processor(LangfuseBaggageProcessor())
```

with

```python
    # First, so the attributes are set before any exporter sees the span.
    provider.add_span_processor(LangfuseBaggageProcessor())
    provider.add_span_processor(PromptLinkProcessor())
```

and import it at the top: `from shared.prompts.link import PromptLinkProcessor`. `shared.prompts.link` imports nothing from `shared.otel`, so there is no cycle.

Add to `tests/shared/test_otel.py`:

```python
def test_init_tracing_registers_the_prompt_link_processor() -> None:
    from shared.prompts.link import PromptLinkProcessor

    with patch("shared.otel.trace.set_tracer_provider") as installed:
        init_tracing(service_name="test-link", endpoint=None)
    provider = installed.call_args.args[0]
    processors = provider._active_span_processor._span_processors  # noqa: SLF001
    assert any(isinstance(p, PromptLinkProcessor) for p in processors)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts/test_link.py tests/shared/test_otel.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Gate, then commit**

```bash
git add shared/prompts/link.py shared/otel.py tests/shared/prompts/test_link.py tests/shared/test_otel.py
git commit -m "feat(prompts): link model-call spans to the prompt versions they used"
```

---

### Task 5: System 2's prompt from the store

**Files:**
- Create: `core/conscious/system2_prompts.py`, `tests/core/conscious/test_system2_prompts.py`, `tests/core/conscious/golden/` (3 `.txt` files, Step 1)
- Modify:
  - `core/conscious/context_assembler.py` (rewrite: `ContextAssembler` becomes the module function `assemble`);
  - `core/conscious/agent.py` (drop the `assembler` parameter);
  - `core/conscious/deps.py` (`TurnDeps.prompts`);
  - `core/conscious/engine.py` (`ConsciousDeps.prompts`, fetch before the run, link around it);
  - `core/conscious/__main__.py` (build the store, log it, pass it, close it);
  - `runner/__main__.py:97,103` (`watch_dirs`: `core/conscious/prompts` → `shared/prompts/defaults`).
- Delete: `core/conscious/prompts/` (both files; Task 1 copied them)
- Test: `tests/core/conscious/test_context_assembler.py`, `test_context_area.py`, `test_agent.py`, `test_engine.py`, `test_effects.py`, `helpers.py`, `tests/integration/test_conscious_pipeline.py`

**Interfaces:**
- Consumes:
  - Task 1: names, `RenderedPrompt`, `PromptRef`, `repo_prompt`.
  - Task 3: `PromptStore`.
  - Task 4: `linked_prompts`.
- Produces, in `core.conscious.system2_prompts`:
  - `VOICE_CHANNELS`, `DEFAULT_PROACTIVITY = "opinionated"`;
  - `System2Prompts(personality, identity, voice_delivery=None, location=None, integrations=None, proactivity=None)` with `.refs() -> tuple[PromptRef, ...]` (personality first);
  - `async fetch_system2_prompts(store, *, who, channel, content_type, area, integrations_available, proactivity_level=DEFAULT_PROACTIVITY) -> System2Prompts`;
  - `repo_system2_prompts(...)`, which takes the same keywords and is sync.
- Produces, in `core.conscious.context_assembler`: `assemble(prompts, *, who, now=None, relevant_context=None, tz_name="UTC", routine_hint="") -> str`.
- `build_system2_agent(*, toolsets, max_tokens, timeout_s, spend)`.
- `TurnDeps.prompts: System2Prompts` (required, placed after `received_ms`).
- `ConsciousDeps.prompts: PromptStore = field(default_factory=PromptStore)`.

- [ ] **Step 1: Capture today's prompt as goldens (before changing any code)**

Write this to the scratchpad and run it from the worktree root with `uv run python <scratchpad>/gen_golden.py`. It records what today's `ContextAssembler` produces for three turns. It then applies Ruling 2, the one intended change: the personality and voice-delivery files lose their terminating newline.

```python
"""One-off: today's System 2 instructions for three turns, as goldens."""

from datetime import UTC, datetime
from pathlib import Path

from core.conscious.context_assembler import ContextAssembler
from core.memory.vector_store import ContextMetadata, SearchResult
from tests.core.conscious.helpers import identity

NOW = datetime(2026, 10, 14, 18, 30, tzinfo=UTC)
RECALL = [
    SearchResult(
        id="sr-1",
        score=0.82,
        content="Sir prefers dim lighting after 8pm",
        semantic_key="dim lighting",
        metadata=ContextMetadata(
            type="semantic", source="t", entities="", timestamp=0.0,
            significance=1.0, retrieval_count=0,
        ),
    )
]
CASES = {
    "sir_voice_full": dict(
        identity=identity("sir"), integrations_available=True, now=NOW,
        relevant_context=RECALL, channel="satellite", content_type="audio",
        area="kitchen", tz_name="America/Denver", routine_hint="Routine hint: evening_dim",
    ),
    "sir_text_plain": dict(
        identity=identity("sir"), now=NOW, channel="web_pwa", content_type="text",
    ),
    "guest_voice_area": dict(
        identity=identity("guest"), integrations_available=True, now=NOW,
        relevant_context=RECALL, channel="satellite", content_type="audio", area="office",
    ),
}
src = Path("core/conscious/prompts")
personality = (src / "personality.md").read_text()
voice = (src / "voice_delivery.md").read_text()
out = Path("tests/core/conscious/golden")
out.mkdir(exist_ok=True)
for name, kwargs in CASES.items():
    text = ContextAssembler().assemble(**kwargs)
    text = text.replace(personality, personality.removesuffix("\n"))
    text = text.replace(voice, voice.removesuffix("\n"))
    (out / f"{name}.txt").write_text(text, encoding="utf-8")
print(sorted(p.name for p in out.iterdir()))
```

Expected: `['guest_voice_area.txt', 'sir_text_plain.txt', 'sir_voice_full.txt']`. Open `sir_voice_full.txt` once and check that it contains the personality, the voice delivery, `## Identity`, `## Location` with `kitchen` twice, `## Current Time`, `## Integrations`, `## Relevant Context`, `## Proactivity Level: opinionated` and the routine hint, in that order.

- [ ] **Step 2: Write the failing tests**

`tests/core/conscious/test_system2_prompts.py`:

```python
"""System 2's prompt: which sections a turn gets, from the store, assembled as before."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.conscious.context_assembler import assemble
from core.conscious.system2_prompts import (
    System2Prompts,
    fetch_system2_prompts,
    repo_system2_prompts,
)
from core.memory.vector_store import ContextMetadata, SearchResult
from shared.prompts.names import SYSTEM2_IDENTITY_SIR, SYSTEM2_PERSONALITY
from shared.prompts.store import PromptStore
from tests.shared.prompts.fakes import FakeLangfuse

GOLDEN = Path(__file__).parent / "golden"
NOW = datetime(2026, 10, 14, 18, 30, tzinfo=UTC)
RECALL = [
    SearchResult(
        id="sr-1",
        score=0.82,
        content="Sir prefers dim lighting after 8pm",
        semantic_key="dim lighting",
        metadata=ContextMetadata(
            type="semantic",
            source="t",
            entities="",
            timestamp=0.0,
            significance=1.0,
            retrieval_count=0,
        ),
    )
]


@pytest.mark.parametrize(
    ("golden", "turn", "data"),
    [
        (
            "sir_voice_full",
            {
                "who": "sir",
                "channel": "satellite",
                "content_type": "audio",
                "area": "kitchen",
                "integrations_available": True,
            },
            {
                "relevant_context": RECALL,
                "tz_name": "America/Denver",
                "routine_hint": "Routine hint: evening_dim",
            },
        ),
        (
            "sir_text_plain",
            {
                "who": "sir",
                "channel": "web_pwa",
                "content_type": "text",
                "area": None,
                "integrations_available": False,
            },
            {},
        ),
        (
            "guest_voice_area",
            {
                "who": "guest",
                "channel": "satellite",
                "content_type": "audio",
                "area": "office",
                "integrations_available": True,
            },
            {"relevant_context": RECALL},
        ),
    ],
)
def test_the_repo_defaults_assemble_todays_prompt(
    golden: str, turn: dict[str, object], data: dict[str, object]
) -> None:
    prompts = repo_system2_prompts(**turn)  # type: ignore[arg-type]
    who = str(turn["who"])
    text = assemble(prompts, who=who, now=NOW, **data)  # type: ignore[arg-type]
    assert text == (GOLDEN / f"{golden}.txt").read_text(encoding="utf-8")


def test_a_text_turn_on_a_voice_channel_gets_no_voice_section() -> None:
    prompts = repo_system2_prompts(
        who="sir",
        channel="satellite",
        content_type="text",
        area=None,
        integrations_available=False,
    )
    assert prompts.voice_delivery is None


def test_a_guest_gets_no_integrations_or_proactivity() -> None:
    prompts = repo_system2_prompts(
        who="guest",
        channel="web_pwa",
        content_type="text",
        area=None,
        integrations_available=True,
    )
    assert (prompts.integrations, prompts.proactivity) == (None, None)


def test_refs_put_the_personality_first() -> None:
    prompts = repo_system2_prompts(
        who="sir", channel="web_pwa", content_type="text", area=None, integrations_available=False
    )
    refs = prompts.refs()
    assert refs[0].name == SYSTEM2_PERSONALITY
    assert SYSTEM2_IDENTITY_SIR in {r.name for r in refs}


@pytest.mark.asyncio
async def test_fetch_serves_langfuse_text_for_each_section() -> None:
    store = PromptStore(langfuse=FakeLangfuse({SYSTEM2_PERSONALITY: "You are Jeeves."}))
    prompts = await fetch_system2_prompts(
        store,
        who="sir",
        channel="web_pwa",
        content_type="text",
        area=None,
        integrations_available=False,
    )
    assert isinstance(prompts, System2Prompts)
    assert (prompts.personality.text, prompts.personality.source) == ("You are Jeeves.", "langfuse")
    assert prompts.identity.source == "repo"  # unseeded in the fake
```

In `tests/core/conscious/test_engine.py`, add (and change `_deps` as in Step 3):

```python
@pytest.mark.asyncio
async def test_the_turns_instructions_come_from_the_prompt_store() -> None:
    script = Script("ok")
    store = PromptStore(langfuse=FakeLangfuse({SYSTEM2_PERSONALITY: "You are Jeeves."}))

    await ConsciousEngine(deps=_deps(script, prompts=store)).process_request(_request())

    assert script.instructions().startswith("You are Jeeves.")


@pytest.mark.asyncio
async def test_the_run_is_linked_to_the_turns_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[PromptRef, ...]] = []

    @contextmanager
    def _linked(*refs: PromptRef) -> Iterator[None]:
        seen.append(refs)
        yield

    monkeypatch.setattr("core.conscious.engine.linked_prompts", _linked)
    store = PromptStore(langfuse=FakeLangfuse({SYSTEM2_PERSONALITY: "You are Jeeves."}))

    await ConsciousEngine(deps=_deps(Script("hi"), prompts=store)).process_request(_request())

    (refs,) = seen
    assert refs[0] == PromptRef(SYSTEM2_PERSONALITY, 7, "langfuse")
```

with the imports `from shared.prompts.names import SYSTEM2_PERSONALITY`, `from shared.prompts.store import PromptStore`, `from shared.prompts.template import PromptRef` and `from tests.shared.prompts.fakes import FakeLangfuse`.

- [ ] **Step 3: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/conscious/test_system2_prompts.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'core.conscious.system2_prompts'`.

- [ ] **Step 4: Implement**

`core/conscious/system2_prompts.py`:

```python
"""Which prose sections one System 2 turn includes, rendered from the prompt store.

Every input is known before the run starts (who is asking, the channel, the content
type, the satellite's area, whether integrations answered), so the engine renders the
sections here and the agent's instructions function stays sync and does no I/O.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from shared.prompts.names import (
    SYSTEM2_IDENTITY_GUEST,
    SYSTEM2_IDENTITY_SIR,
    SYSTEM2_INTEGRATIONS,
    SYSTEM2_LOCATION,
    SYSTEM2_PERSONALITY,
    SYSTEM2_PROACTIVITY,
    SYSTEM2_VOICE_DELIVERY,
)
from shared.prompts.template import repo_prompt

if TYPE_CHECKING:
    from shared.prompts.store import PromptStore
    from shared.prompts.template import PromptRef, RenderedPrompt

# Channels that speak the reply (TTS).
VOICE_CHANNELS: Final = frozenset({"web_pwa", "satellite"})
DEFAULT_PROACTIVITY: Final = "opinionated"


@dataclass(frozen=True)
class System2Prompts:
    """One turn's rendered sections; None is a section this turn does not include."""

    personality: RenderedPrompt
    identity: RenderedPrompt
    voice_delivery: RenderedPrompt | None = None
    location: RenderedPrompt | None = None
    integrations: RenderedPrompt | None = None
    proactivity: RenderedPrompt | None = None

    def refs(self) -> tuple[PromptRef, ...]:
        """Every section's version, personality first: the one a trace links to."""
        sections = (
            self.personality,
            self.voice_delivery,
            self.identity,
            self.location,
            self.integrations,
            self.proactivity,
        )
        return tuple(s.ref for s in sections if s is not None)


def _wanted(
    *,
    who: str,
    channel: str,
    content_type: str,
    area: str | None,
    integrations_available: bool,
    proactivity_level: str,
) -> dict[str, tuple[str, dict[str, object]]]:
    """Section field → (prompt name, variables), for the sections this turn includes."""
    sir = who == "sir"
    wanted: dict[str, tuple[str, dict[str, object]]] = {
        "personality": (SYSTEM2_PERSONALITY, {}),
        "identity": (SYSTEM2_IDENTITY_SIR if sir else SYSTEM2_IDENTITY_GUEST, {}),
    }
    # Only when a spoken reply will actually be produced, not for typed web sessions.
    if content_type == "audio" and channel in VOICE_CHANNELS:
        wanted["voice_delivery"] = (SYSTEM2_VOICE_DELIVERY, {})
    if area:
        wanted["location"] = (SYSTEM2_LOCATION, {"area": area})
    if sir and integrations_available:
        wanted["integrations"] = (SYSTEM2_INTEGRATIONS, {})
    if sir:
        wanted["proactivity"] = (SYSTEM2_PROACTIVITY, {"level": proactivity_level})
    return wanted


async def fetch_system2_prompts(
    store: PromptStore,
    *,
    who: str,
    channel: str,
    content_type: str,
    area: str | None,
    integrations_available: bool,
    proactivity_level: str = DEFAULT_PROACTIVITY,
) -> System2Prompts:
    """This turn's sections, from *store*."""
    wanted = _wanted(
        who=who,
        channel=channel,
        content_type=content_type,
        area=area,
        integrations_available=integrations_available,
        proactivity_level=proactivity_level,
    )
    rendered = await asyncio.gather(*(store.get(n, **v) for n, v in wanted.values()))
    return System2Prompts(**dict(zip(wanted, rendered, strict=True)))


def repo_system2_prompts(
    *,
    who: str,
    channel: str,
    content_type: str,
    area: str | None,
    integrations_available: bool,
    proactivity_level: str = DEFAULT_PROACTIVITY,
) -> System2Prompts:
    """This turn's sections from the repo defaults, without a store (tests, tools)."""
    wanted = _wanted(
        who=who,
        channel=channel,
        content_type=content_type,
        area=area,
        integrations_available=integrations_available,
        proactivity_level=proactivity_level,
    )
    return System2Prompts(**{f: repo_prompt(n, **v) for f, (n, v) in wanted.items()})
```

`core/conscious/context_assembler.py`, rewritten. Keep the module docstring's first paragraph and the two-stage-context note, and replace the class with:

```python
from __future__ import annotations

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from datetime import datetime

    from core.conscious.system2_prompts import System2Prompts
    from core.memory.vector_store import SearchResult


def assemble(
    prompts: System2Prompts,
    *,
    who: str,
    now: datetime | None = None,
    relevant_context: list[SearchResult] | None = None,
    tz_name: str = "UTC",
    routine_hint: str = "",
) -> str:
    """Build System 2's instructions for one turn: the rendered prose sections, with the
    turn's data (time, recall, routine hint) between them.

    The tools are not listed here: the model is offered them as tool definitions,
    under the names it must call them by (#299).
    """
    parts: list[str] = [prompts.personality.text]
    if prompts.voice_delivery is not None:
        parts.append(prompts.voice_delivery.text)
    parts.append("\n" + prompts.identity.text)
    if prompts.location is not None:
        parts.append("\n" + prompts.location.text)

    # Always, when known: time-based triggers and reminders need it.
    if now is not None:
        local = now.astimezone(ZoneInfo(tz_name))
        stamp = f"{local.strftime('%A')} {local.isoformat(timespec='seconds')}"
        parts.append(f"\n## Current Time\n{stamp} ({tz_name})")

    if prompts.integrations is not None:
        parts.append("\n" + prompts.integrations.text)

    # Involuntary recall, sir only.
    if who == "sir" and relevant_context:
        lines = [
            f"- [{r.metadata.type}] {r.content} (relevance: {r.score:.2f})"
            for r in relevant_context
        ]
        parts.append("\n## Relevant Context\n" + "\n".join(lines))

    if prompts.proactivity is not None:
        parts.append("\n" + prompts.proactivity.text)

    prompt = "\n".join(parts)
    if routine_hint:
        prompt += "\n\n" + routine_hint
    return prompt
```

`core/conscious/deps.py`:
- Under `TYPE_CHECKING`, add `from core.conscious.system2_prompts import System2Prompts`.
- In `TurnDeps`, after `received_ms: int`, add `prompts: System2Prompts`.
- Extend the docstring with one sentence: "``prompts`` is the turn's prose sections, rendered from the prompt store before the run."

`core/conscious/agent.py`: remove the `assembler` parameter and its `TYPE_CHECKING` import, import `assemble` from `core.conscious.context_assembler`, and make the instructions function:

```python
    @agent.instructions
    def _instructions(ctx: RunContext[TurnDeps]) -> str:
        d = ctx.deps
        return assemble(
            d.prompts,
            who=d.who,
            now=d.now,
            relevant_context=d.recall or None,
            tz_name=d.tz_name,
            routine_hint=d.routine_hint,
        )
```

`core/conscious/engine.py`:
- Add the field `prompts: PromptStore = field(default_factory=PromptStore)` to `ConsciousDeps`, last. Import `PromptStore` at runtime; it is a default factory.
- Add a docstring line: "``prompts`` serves the turn's prose; the default serves the repo defaults."
- In `ConsciousEngine.__init__`: `self._prompts = deps.prompts`.
- In `process_request`, replace the `TurnDeps(...)` block's `integration_tools=await self._integration_tools(identity.identity),` by fetching it first:

```python
        integration_tools = await self._integration_tools(identity.identity)
        prompts = await fetch_system2_prompts(
            self._prompts,
            who=identity.identity,
            channel=request.channel,
            content_type=request.content_type,
            area=request.area,
            integrations_available=bool(integration_tools),
        )
        deps = TurnDeps(
            ...unchanged fields...,
            prompts=prompts,
            integration_tools=integration_tools,
        )
```

- In `_run`, link the run:

```python
            with (
                langfuse_attributes(
                    session_id=request.session_id, user_id=deps.who, tags=[request.channel]
                ),
                linked_prompts(*deps.prompts.refs()),
            ):
```

  with `from shared.prompts.link import linked_prompts` and `from core.conscious.system2_prompts import fetch_system2_prompts`.

`core/conscious/__main__.py`:
- Import `from shared.prompts.store import PromptStore`.
- After `r` is created: `prompts = PromptStore.from_config(config, redis=r)`. Then one log line in the file's existing style naming `prompts.describe()`, e.g. `log.info("Prompts: {}", prompts.describe())` if the file uses `{}`.
- Remove `assembler=ContextAssembler(),` and its import.
- Pass `prompts=prompts` to `ConsciousDeps(...)`.
- Add `"prompt store": prompts.aclose,` to the `teardown(closers={...})` mapping before `"redis"`.

`runner/__main__.py` lines 97 and 103: replace `"core/conscious/prompts"` with `"shared/prompts/defaults"`.

Delete `core/conscious/prompts/` with `git rm -r core/conscious/prompts`.

**Port the existing tests:**
- `tests/core/conscious/helpers.py:make_deps`: add `"prompts": repo_system2_prompts(who=who, channel="web_pwa", content_type="text", area=None, integrations_available=False),` to `fields`, and import `repo_system2_prompts`.
- `test_engine.py`, `test_effects.py`, `tests/integration/test_conscious_pipeline.py`: delete `assembler=ContextAssembler(),` and the import.
- `test_agent.py`: its fixture at line ~48 writes a personality file for `ContextAssembler(personality_path=...)`. Delete the fixture and its parameter, and drop `assembler=` from the three `build_system2_agent(...)` calls. A test that asserted the custom personality text reaches the model now passes `prompts=` through `make_deps` with `System2Prompts(personality=RenderedPrompt("You are Alfred, a butler.", SYSTEM2_PERSONALITY, None, "repo"), identity=repo_prompt(SYSTEM2_IDENTITY_SIR))` and asserts the same text.
- `test_context_assembler.py` and `test_context_area.py`: port every test to the new API and keep each assertion.
  - Build `prompts = repo_system2_prompts(who=..., channel=..., content_type=..., area=..., integrations_available=...)` from the arguments the old test passed to `assemble`.
  - Then call `assemble(prompts, who=..., now=..., relevant_context=..., tz_name=..., routine_hint=...)`.
  - Where the old `assembler` fixture wrote `"You are Alfred, a butler."` as the personality, assert on the repo personality's first line instead: `repo_text(SYSTEM2_PERSONALITY).splitlines()[0]`.

  For example:

  ```python
  # before
  prompt = assembler.assemble(identity=_guest(), integrations_available=True)
  assert "## Integrations" not in prompt
  # after
  prompts = repo_system2_prompts(
      who="guest", channel="", content_type="text", area=None, integrations_available=True
  )
  assert "## Integrations" not in assemble(prompts, who="guest")
  ```

  A test that only checked `ContextAssembler` read a file (`personality_path`) is deleted. The golden test covers the defaults.

- [ ] **Step 5: Run the narrow tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/conscious tests/integration/test_conscious_pipeline.py -q -p no:randomly`
Expected: all pass. Then `git grep -n "ContextAssembler\|conscious/prompts"`. Expected: matches only under `docs/` (Task 9 fixes `docs/architecture.md`).

- [ ] **Step 6: Gate, then commit**

```bash
git add -A core/conscious runner/__main__.py tests/core/conscious tests/integration/test_conscious_pipeline.py
git commit -m "feat(conscious): System 2's prose sections come from the prompt store"
```

---

### Task 6: The Librarian's prompts from the store

**Files:**
- Modify:
  - `core/librarian/agents.py`: delete `ANALYSE_INSTRUCTIONS`, `EXTRACT_INSTRUCTIONS`, `COMPRESS_INSTRUCTIONS`, `resolve_instructions` and `pattern_instructions`; `LibrarianTask.instructions` becomes a `RenderedPrompt`; `ask` links the run.
  - `core/librarian/consolidator.py`: the `prompts` kwarg and the five call sites at ~322, ~410, ~488, ~587 and ~780.
  - `core/librarian/__main__.py` (~line 84): pass the store, close it.
  - `core/conscious/__main__.py` (~line 324): pass Task 5's `prompts` to `Librarian(...)`.
- Test: `tests/core/librarian/test_agents.py`, any other test that builds `LibrarianTask` or reads `.instructions`, `tests/shared/prompts/test_template.py` (delete `test_librarian_defaults_match_todays_builders`)

**Interfaces:**
- Consumes:
  - Task 1: `RenderedPrompt`, `repo_prompt`, the `LIBRARIAN_*` names.
  - Task 3: `PromptStore`.
  - Task 4: `linked_prompts`.
- Produces:
  - `LibrarianTask(instructions: RenderedPrompt, expected_items: int | None = None)`;
  - `Librarian(..., prompts: PromptStore | None = None)`, where None means `PromptStore()` (repo defaults).

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/librarian/test_agents.py`:

```python
@pytest.mark.asyncio
async def test_ask_sends_the_rendered_prompt_and_links_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[PromptRef, ...]] = []

    @contextmanager
    def _linked(*refs: PromptRef) -> Iterator[None]:
        seen.append(refs)
        yield

    monkeypatch.setattr("core.librarian.agents.linked_prompts", _linked)
    script = Script([_ANALYSIS])
    agents = build_librarian_agents(script.model)
    prompt = RenderedPrompt("Analyse these.", LIBRARIAN_ANALYSE, 3, "langfuse")

    await agents.ask(
        agents.analyse, "1. kitchen on", LibrarianTask(prompt, expected_items=1), max_tokens=300
    )

    assert script.instructions() == "Analyse these."
    assert seen == [(PromptRef(LIBRARIAN_ANALYSE, 3, "langfuse"),)]
```

If `Script.instructions()` returns more than the instructions text (check `tests/llm_fakes.py`), use `in` instead of `==`.

Replace the two content tests at ~194 and ~199:

```python
def test_the_resolve_prompt_states_the_contradiction_threshold() -> None:
    text = repo_prompt(LIBRARIAN_RESOLVE, min_observations=5, min_days=14).text
    assert "at least 5" in text and "at least 14 days" in text


def test_the_pattern_prompt_keeps_thresholds_zone_and_the_reflex_paragraph() -> None:
    text = repo_prompt(
        LIBRARIAN_PATTERNS, min_occurrences=3, min_days=7, tz_name="America/Los_Angeles"
    ).text
    assert "at least 3 occurrences" in text
    assert "at least 7 different days" in text
    assert "America/Los_Angeles" in text
    assert "source 'reflex'" in text
```

Keep the old tests' remaining assertions, rewritten against these texts.

Add to `tests/core/librarian/test_consolidator.py`, built the way that file's existing tests build a `Librarian` with a scripted model:

```python
@pytest.mark.asyncio
async def test_a_pass_uses_the_prompt_stores_text(...) -> None:
    store = PromptStore(langfuse=FakeLangfuse({LIBRARIAN_EXTRACT: "Extract carefully."}))
    # build the Librarian exactly as the neighbouring extract test does, plus prompts=store
    ...
    assert "Extract carefully." in script.instructions()
```

Copy the neighbouring extract test whole: same fixtures, same scripted replies, same assertions. Add `prompts=store` to its `Librarian(...)` call and append the final assertion. The test must exercise `_update_semantic_memory`.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/librarian -q -p no:randomly`
Expected: failures on the new tests (`ImportError` for `RenderedPrompt` use or `linked_prompts` missing in `core.librarian.agents`).

- [ ] **Step 3: Implement**

`core/librarian/agents.py`:
- Delete the three constants and two functions.
- Change the module docstring's second paragraph to begin: "The instructions come from the prompt store (``shared.prompts``) and say what to return."
- Import `from shared.prompts.link import linked_prompts`, and `RenderedPrompt` under `TYPE_CHECKING`.
- Change the task:

```python
@dataclass(frozen=True)
class LibrarianTask:
    """One call's rendered prompt and, for analysis, how many elements it must return."""

    instructions: RenderedPrompt
    expected_items: int | None = None
```

In `ask`:

```python
        try:
            with linked_prompts(task.instructions.ref):
                result = await agent.run(
                    prompt,
                    deps=task,
                    model=self.model,
                    model_settings=ModelSettings(max_tokens=max_tokens, timeout=self.timeout_s),
                )
        finally:
            await self._after_ask()
```

In `_agent`: `return ctx.deps.instructions.text`.

`core/librarian/consolidator.py`:
- Add `prompts: PromptStore | None = None` to `Librarian.__init__` after `notifier`, with `self._prompts = prompts if prompts is not None else PromptStore()`.
- Import `PromptStore` at runtime and the `LIBRARIAN_*` names. Drop the imports of the deleted constants.
- At each call site, fetch the prompt inside the existing `try` and pass it:

```python
# _analyse_batch
                LibrarianTask(
                    await self._prompts.get(LIBRARIAN_ANALYSE), expected_items=len(summaries)
                ),
# _resolve_conflicts
                LibrarianTask(
                    await self._prompts.get(
                        LIBRARIAN_RESOLVE,
                        min_observations=conflict_min_observations,
                        min_days=conflict_min_days,
                    )
                ),
# _update_semantic_memory
                LibrarianTask(await self._prompts.get(LIBRARIAN_EXTRACT)),
# _compress_and_migrate
                    LibrarianTask(await self._prompts.get(LIBRARIAN_COMPRESS)),
# _detect_patterns
                LibrarianTask(
                    await self._prompts.get(
                        LIBRARIAN_PATTERNS,
                        min_occurrences=self._pattern_min_occurrences,
                        min_days=self._pattern_min_days,
                        tz_name=tz_name,
                    )
                ),
```

Use whatever local names each method already uses for the thresholds. `store.get` never raises for a reachable-or-not Langfuse, so the existing `except` clauses keep their meaning.

`core/librarian/__main__.py`:
- After `r` is created: `prompts = PromptStore.from_config(config, redis=r)`.
- Pass `prompts=prompts` to `Librarian(...)`.
- Add `"prompt store": prompts.aclose,` to its `teardown(closers=...)` before `"redis"`.

`core/conscious/__main__.py`: add `prompts=prompts,` to the `Librarian(...)` call.

**Port the tests:**
- In `tests/core/librarian/test_agents.py`, add a helper and replace every `LibrarianTask("…"` with `_task("…"`:

```python
def _task(text: str, expected_items: int | None = None) -> LibrarianTask:
    return LibrarianTask(RenderedPrompt(text, LIBRARIAN_ANALYSE, None, "repo"), expected_items)
```

- Run `git grep -n "LibrarianTask(\|\.instructions\b" tests evals` and fix every remaining site the same way. A test reading `task.instructions` as a string reads `task.instructions.text`.
- Delete `test_librarian_defaults_match_todays_builders` and its now-unused imports from `tests/shared/prompts/test_template.py`.

- [ ] **Step 4: Run the narrow tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/librarian tests/shared/prompts tests/evals/test_memory_metrics.py -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Gate, then commit**

```bash
git add -A core/librarian core/conscious/__main__.py tests/core/librarian tests/shared/prompts
git commit -m "feat(librarian): the five Librarian prompts come from the prompt store"
```

---

### Task 7: `alfred prompts push | diff | pull`

**Files:**
- Create: `shared/prompts/cli.py`, `tests/shared/prompts/test_cli.py`
- Modify: `alfred_cli/main.py` (register the sub-app)

**Interfaces:**
- Consumes:
  - Task 1: `PROMPTS`, `repo_text`, `unusable_reason`, `DEFAULTS_DIR`.
  - Task 2: `PromptAdmin`, `LangfuseClient`, `LangfuseError`.
  - Task 3: `DEFAULT_PROMPT_LABEL`.
- Produces:
  - `prompts_app: typer.Typer`;
  - `async push_prompts(client, names, *, promote) -> list[str]`;
  - `async diff_prompts(client, names, *, label) -> tuple[list[str], bool]`;
  - `async pull_prompts(client, names, *, label, defaults_dir=DEFAULTS_DIR) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

`tests/shared/prompts/test_cli.py`:

```python
"""alfred prompts: push the defaults, show drift, pull Langfuse's versions back."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from alfred_cli.main import app
from shared.prompts.cli import diff_prompts, pull_prompts, push_prompts
from shared.prompts.names import SYSTEM2_LOCATION, SYSTEM2_PERSONALITY
from shared.prompts.template import repo_text
from tests.shared.prompts.fakes import FakeLangfuse

if TYPE_CHECKING:
    from pathlib import Path

P = SYSTEM2_PERSONALITY


@pytest.mark.asyncio
async def test_push_creates_a_version_only_where_the_text_differs() -> None:
    fake = FakeLangfuse({P: repo_text(P)}, version=3, labels=("latest",))
    lines = await push_prompts(fake, [P, SYSTEM2_LOCATION], promote=False)

    assert fake.created == [(SYSTEM2_LOCATION, repo_text(SYSTEM2_LOCATION), [])]
    assert lines == [f"{P}: v3 unchanged", f"{SYSTEM2_LOCATION}: pushed v4"]


@pytest.mark.asyncio
async def test_push_promote_labels_new_and_unchanged_versions_production() -> None:
    fake = FakeLangfuse({P: repo_text(P)}, version=3, labels=("candidate", "latest"))
    lines = await push_prompts(fake, [P, SYSTEM2_LOCATION], promote=True)

    assert fake.labelled == [(P, 3, ["candidate", "production"])]
    assert fake.created == [(SYSTEM2_LOCATION, repo_text(SYSTEM2_LOCATION), ["production"])]
    assert lines == [f"{P}: v3 unchanged, promoted", f"{SYSTEM2_LOCATION}: pushed v4 (production)"]


@pytest.mark.asyncio
async def test_push_promote_leaves_an_already_promoted_version_alone() -> None:
    fake = FakeLangfuse({P: repo_text(P)}, version=3, labels=("production", "latest"))
    await push_prompts(fake, [P], promote=True)
    assert (fake.created, fake.labelled) == ([], [])


@pytest.mark.asyncio
async def test_diff_reports_drift_and_unseeded_prompts() -> None:
    fake = FakeLangfuse({P: repo_text(P) + "\nBe brief."})
    lines, drifted = await diff_prompts(fake, [P, SYSTEM2_LOCATION], label="production")

    assert drifted
    assert "+Be brief." in lines
    assert f"{SYSTEM2_LOCATION}: not in Langfuse at label production" in lines


@pytest.mark.asyncio
async def test_diff_is_clean_when_langfuse_matches() -> None:
    fake = FakeLangfuse({P: repo_text(P)})
    assert await diff_prompts(fake, [P], label="production") == ([], False)


@pytest.mark.asyncio
async def test_pull_writes_langfuse_text_with_a_terminating_newline(tmp_path: Path) -> None:
    fake = FakeLangfuse({P: "You are Jeeves.", SYSTEM2_LOCATION: "In the {{room}}."})
    lines = await pull_prompts(
        fake, [P, SYSTEM2_LOCATION], label="production", defaults_dir=tmp_path
    )

    assert (tmp_path / f"{P}.md").read_text(encoding="utf-8") == "You are Jeeves.\n"
    assert not (tmp_path / f"{SYSTEM2_LOCATION}.md").exists()
    assert lines[0] == f"{P}: wrote v7"
    assert "room" in lines[1] and "kept" in lines[1]


def test_the_cli_needs_the_langfuse_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    result = CliRunner().invoke(app, ["prompts", "diff"])
    assert result.exit_code == 2
    assert "LANGFUSE_HOST" in result.output


def test_an_unknown_prompt_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    result = CliRunner().invoke(app, ["prompts", "diff", "no-such-prompt"])
    assert result.exit_code == 2
    assert "no-such-prompt" in result.output
```

If this typer version's `CliRunner` separates stderr, read `result.stderr` where `result.output` lacks the message. Match the existing `tests/evals` CLI tests.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts/test_cli.py -q -p no:randomly`
Expected: `ModuleNotFoundError: No module named 'shared.prompts.cli'`.

- [ ] **Step 3: Implement**

`shared/prompts/cli.py`:

```python
"""``alfred prompts``: keep the repo defaults and Langfuse in step.

``push`` uploads a default as a new version where its text differs from Langfuse's
``latest`` (``--promote`` also labels it ``production``); ``diff`` shows drift against a
label and exits 1 when there is any; ``pull`` writes a label's versions back to the
defaults. Reads ``LANGFUSE_HOST`` and the project keys from the environment.
"""

from __future__ import annotations

import asyncio
import difflib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

import typer

from shared.config import DEFAULT_PROMPT_LABEL
from shared.prompts.langfuse import LangfuseClient, LangfuseError
from shared.prompts.names import PROMPTS
from shared.prompts.template import DEFAULTS_DIR, repo_text, unusable_reason

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shared.prompts.langfuse import PromptAdmin, PromptFetcher

CLI_TIMEOUT_S: Final = 10.0
PRODUCTION: Final = DEFAULT_PROMPT_LABEL
LATEST: Final = "latest"
PUSH_COMMIT_MESSAGE: Final = "alfred prompts push"

prompts_app = typer.Typer(
    no_args_is_help=True, help="Alfred's prompts in Langfuse: push, diff and pull."
)

NamesArg = Annotated[
    list[str] | None, typer.Argument(help="Prompt names (default: every prompt)")
]
LabelOpt = Annotated[str, typer.Option(help="The Langfuse label to compare with")]


async def push_prompts(client: PromptAdmin, names: Sequence[str], *, promote: bool) -> list[str]:
    """Create a version of each prompt whose default differs from ``latest``."""
    lines: list[str] = []
    for name in names:
        text = repo_text(name)
        latest = await client.get(name, label=LATEST)
        if latest is not None and latest.text == text:
            if promote and PRODUCTION not in latest.labels:
                kept = [label for label in latest.labels if label != LATEST]
                await client.set_labels(name, latest.version, [*kept, PRODUCTION])
                lines.append(f"{name}: v{latest.version} unchanged, promoted")
            else:
                lines.append(f"{name}: v{latest.version} unchanged")
            continue
        created = await client.create(
            name,
            text,
            labels=[PRODUCTION] if promote else [],
            commit_message=PUSH_COMMIT_MESSAGE,
        )
        lines.append(f"{name}: pushed v{created.version}" + (" (production)" if promote else ""))
    return lines


async def diff_prompts(
    client: PromptFetcher, names: Sequence[str], *, label: str
) -> tuple[list[str], bool]:
    """Unified diffs from each default to *label*'s version, and whether any differ."""
    lines: list[str] = []
    drifted = False
    for name in names:
        remote = await client.get(name, label=label)
        if remote is None:
            lines.append(f"{name}: not in Langfuse at label {label}")
            drifted = True
            continue
        if remote.text is None:
            lines.append(f"{name}: v{remote.version} is not a text prompt")
            drifted = True
            continue
        text = repo_text(name)
        if remote.text == text:
            continue
        drifted = True
        lines.extend(
            difflib.unified_diff(
                text.splitlines(),
                remote.text.splitlines(),
                fromfile=f"repo/{name}",
                tofile=f"langfuse/{name}@{label} (v{remote.version})",
                lineterm="",
            )
        )
    return lines, drifted


async def pull_prompts(
    client: PromptFetcher,
    names: Sequence[str],
    *,
    label: str,
    defaults_dir: Path = DEFAULTS_DIR,
) -> list[str]:
    """Write *label*'s version of each prompt over its default, unless it is unusable."""
    lines: list[str] = []
    for name in names:
        remote = await client.get(name, label=label)
        if remote is None:
            lines.append(f"{name}: not in Langfuse at label {label}; kept")
            continue
        reason = unusable_reason(name, remote.type, remote.text)
        if reason is not None or remote.text is None:
            lines.append(f"{name}: v{remote.version} {reason}; kept")
            continue
        path = defaults_dir / f"{name}.md"
        if path.exists() and path.read_text(encoding="utf-8") == remote.text + "\n":
            lines.append(f"{name}: v{remote.version} unchanged")
            continue
        path.write_text(remote.text + "\n", encoding="utf-8")
        lines.append(f"{name}: wrote v{remote.version}")
    repo_text.cache_clear()
    return lines


def _client() -> LangfuseClient:
    values = {
        name: os.environ.get(name, "").strip()
        for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        typer.echo(f"Set {', '.join(missing)} first.", err=True)
        raise typer.Exit(2)
    return LangfuseClient(
        values["LANGFUSE_HOST"],
        public_key=values["LANGFUSE_PUBLIC_KEY"],
        secret_key=values["LANGFUSE_SECRET_KEY"],
        timeout_s=CLI_TIMEOUT_S,
    )


def _selected(names: list[str] | None) -> list[str]:
    unknown = [n for n in names or [] if n not in PROMPTS]
    if unknown:
        typer.echo(f"Unknown prompts: {', '.join(unknown)}", err=True)
        raise typer.Exit(2)
    return list(names) if names else list(PROMPTS)


@prompts_app.command()
def push(
    names: NamesArg = None,
    promote: Annotated[
        bool, typer.Option("--promote", help="Label the pushed versions production")
    ] = False,
) -> None:
    """Upload repo defaults that differ from Langfuse's latest version."""
    selected = _selected(names)
    client = _client()

    async def go() -> list[str]:
        try:
            return await push_prompts(client, selected, promote=promote)
        finally:
            await client.aclose()

    _echo(go)


@prompts_app.command()
def diff(names: NamesArg = None, label: LabelOpt = PRODUCTION) -> None:
    """Show where Langfuse's version at LABEL differs from the repo; exit 1 on drift."""
    selected = _selected(names)
    client = _client()

    async def go() -> tuple[list[str], bool]:
        try:
            return await diff_prompts(client, selected, label=label)
        finally:
            await client.aclose()

    try:
        lines, drifted = asyncio.run(go())
    except LangfuseError as exc:
        typer.echo(f"Langfuse: {exc}", err=True)
        raise typer.Exit(1) from exc
    for line in lines:
        typer.echo(line)
    if drifted:
        raise typer.Exit(1)
    typer.echo(f"No drift at label {label}.")


@prompts_app.command()
def pull(names: NamesArg = None, label: LabelOpt = PRODUCTION) -> None:
    """Write Langfuse's versions at LABEL back to the repo defaults."""
    selected = _selected(names)
    client = _client()

    async def go() -> list[str]:
        try:
            return await pull_prompts(client, selected, label=label)
        finally:
            await client.aclose()

    _echo(go)


def _echo(go: Callable[[], Coroutine[Any, Any, list[str]]]) -> None:
    """Run *go* and print the report lines it returns."""
    try:
        lines = asyncio.run(go())
    except LangfuseError as exc:
        typer.echo(f"Langfuse: {exc}", err=True)
        raise typer.Exit(1) from exc
    for line in lines:
        typer.echo(line)
```

Add `Callable` and `Coroutine` to the `TYPE_CHECKING` import from `collections.abc`, and `Any` to the `typing` import.

`alfred_cli/main.py`:

```python
from shared.prompts.cli import prompts_app

app.add_typer(prompts_app, name="prompts")
```

placed beside the `evals_app` registration, matching its import style.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/shared/prompts/test_cli.py -q -p no:randomly`
Expected: all pass. Also check `uv run --no-sync alfred prompts --help`, which lists `push`, `diff` and `pull`.

- [ ] **Step 5: Gate, then commit**

```bash
git add shared/prompts/cli.py alfred_cli/main.py tests/shared/prompts/test_cli.py
git commit -m "feat(prompts): alfred prompts push, diff and pull"
```

---

### Task 8: `alfred evals run --prompt-label`

**Files:**
- Modify:
  - `evals/harness/stack.py`: `LangfuseAccess`, `langfuse_access()`, the `StackConfig.langfuse` field, `container_env`;
  - `evals/harness/orchestrate.py`: `RunOptions.langfuse`, the `StackConfig(...)` at ~326, the scorecard commit line at ~314;
  - `evals/cli.py`: the option on `run` (~118).
- Test: `tests/evals/harness/test_stack.py`, plus `tests/evals/test_cli.py` (or wherever `evals run` options are tested; find it with `git grep -n "allow_stale_home_service" tests`)

**Interfaces:**
- Consumes: none from earlier tasks. The container reads the variables named in Task 3.
- Produces:
  - `LangfuseAccess(host, public_key, secret_key, label)`;
  - `langfuse_access(env: Mapping[str, str], label: str) -> LangfuseAccess`, which raises `StackError` naming the missing variables;
  - the fields `StackConfig.langfuse: LangfuseAccess | None = None` and `RunOptions.langfuse: LangfuseAccess | None = None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/evals/harness/test_stack.py`, using that file's `cfg(tmp_path)` helper:

```python
def test_container_env_serves_repo_prompts_by_default(tmp_path: Path) -> None:
    env = container_env(cfg(tmp_path), proxy_port=9100, fake_ha_port=9200)
    assert "LANGFUSE_HOST" not in env and "ALFRED_PROMPT_LABEL" not in env


def test_container_env_points_prompts_at_langfuse_with_a_label(tmp_path: Path) -> None:
    access = LangfuseAccess(
        host="http://langfuse.test:3300", public_key="pk", secret_key="sk", label="latest"
    )
    env = container_env(
        dataclasses.replace(cfg(tmp_path), langfuse=access), proxy_port=9100, fake_ha_port=9200
    )
    assert env["LANGFUSE_HOST"] == "http://langfuse.test:3300"
    assert (env["LANGFUSE_PUBLIC_KEY"], env["LANGFUSE_SECRET_KEY"]) == ("pk", "sk")
    assert env["ALFRED_PROMPT_LABEL"] == "latest"


def test_a_loopback_langfuse_is_spelled_for_the_container(tmp_path: Path) -> None:
    access = LangfuseAccess(
        host="http://localhost:3300", public_key="pk", secret_key="sk", label="latest"
    )
    env = container_env(
        dataclasses.replace(cfg(tmp_path), langfuse=access), proxy_port=9100, fake_ha_port=9200
    )
    assert env["LANGFUSE_HOST"] == container_reachable("http://localhost:3300")


def test_langfuse_access_names_what_is_missing() -> None:
    with pytest.raises(StackError, match="LANGFUSE_SECRET_KEY"):
        langfuse_access(
            {"LANGFUSE_HOST": "http://langfuse.test", "LANGFUSE_PUBLIC_KEY": "pk"}, "latest"
        )
    access = langfuse_access(
        {
            "LANGFUSE_HOST": "http://langfuse.test",
            "LANGFUSE_PUBLIC_KEY": "pk",
            "LANGFUSE_SECRET_KEY": "sk",
        },
        "latest",
    )
    assert access.label == "latest"
```

Imports: `dataclasses`; `LangfuseAccess`, `langfuse_access` and `StackError` from `evals.harness.stack`; `container_reachable` from `evals.harness.net`.

In the evals CLI test file, add a test in the style of its existing `run` tests: with `LANGFUSE_HOST` unset, `alfred evals run --prompt-label latest --no-build` exits 2 before anything boots, and stderr names `LANGFUSE_HOST`. Patch `run_suites` the way the neighbouring tests do, so nothing starts.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/evals/harness/test_stack.py -q -p no:randomly`
Expected: `ImportError: cannot import name 'LangfuseAccess'`.

- [ ] **Step 3: Implement**

In `evals/harness/stack.py`:

```python
@dataclass(frozen=True)
class LangfuseAccess:
    """Where the eval stack fetches prompts when ``--prompt-label`` is given."""

    host: str
    public_key: str
    secret_key: str
    label: str


def langfuse_access(env: Mapping[str, str], label: str) -> LangfuseAccess:
    """The operator's Langfuse settings for *label*; raises ``StackError`` naming what is
    missing, so a run never silently falls back to the repo prompts it was not asked for."""
    names = ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    values = {name: env.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise StackError(f"--prompt-label needs {', '.join(missing)} in the environment")
    return LangfuseAccess(
        values["LANGFUSE_HOST"], values["LANGFUSE_PUBLIC_KEY"], values["LANGFUSE_SECRET_KEY"], label
    )
```

Add `langfuse: LangfuseAccess | None = None` as the last field of `StackConfig`. In `container_env`, before `return env`:

```python
    if cfg.langfuse is not None:
        env["LANGFUSE_HOST"] = container_reachable(cfg.langfuse.host)
        env["LANGFUSE_PUBLIC_KEY"] = cfg.langfuse.public_key
        env["LANGFUSE_SECRET_KEY"] = cfg.langfuse.secret_key
        env["ALFRED_PROMPT_LABEL"] = cfg.langfuse.label
```

Update the docstring to: "Everything the container needs; nothing from the operator's environment except, with ``--prompt-label``, the Langfuse settings the CLI read."

In `evals/harness/orchestrate.py`:
- Add `langfuse: LangfuseAccess | None = None` as the last field of `RunOptions`.
- Pass `langfuse=opts.langfuse` to `StackConfig(...)`.
- Extend the commit line:

```python
    commit = alfred_commit(REPO_ROOT) + ("" if opts.build else NOT_REBUILT)
    if opts.langfuse is not None:
        commit += f" (prompts: Langfuse label {opts.langfuse.label})"
```

In `evals/cli.py:run`, add the option after `proxy_port`:

```python
    prompt_label: Annotated[
        str | None,
        typer.Option(
            "--prompt-label",
            help="Serve prompts from Langfuse at this label (needs LANGFUSE_HOST and the "
            "project keys in the environment, reachable from the container); "
            "default: the repo defaults",
        ),
    ] = None,
```

Before `opts = RunOptions(...)`:

```python
    try:
        langfuse = langfuse_access(os.environ, prompt_label) if prompt_label else None
    except StackError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
```

Pass `langfuse=langfuse` to `RunOptions(...)`, and add `langfuse_access` to the local import from `evals.harness.stack`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/evals -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Gate, then commit**

```bash
git add evals tests/evals
git commit -m "feat(evals): --prompt-label serves the eval stack's prompts from Langfuse"
```

---

### Task 9: Docs

**Files:**
- Modify: `docs/llm.md`, `docs/deployment.md`, `docs/evals.md`, `docs/architecture.md:673`, `shared/CLAUDE.md`, `CLAUDE.md` (Key Paths)

No hostnames, IPs, domains or keys anywhere. Examples use `http://langfuse-web:3000` and `http://localhost:3300`.

- [ ] **Step 1: `docs/llm.md`.** Add a "Prompts" section covering:
  - where prompt prose lives: Langfuse in production, `shared/prompts/defaults/` otherwise;
  - the 12 names and their variables (copy them from `shared/prompts/names.py`);
  - the fallback order and the 60 s lazy refresh;
  - what is refused (chat prompts, undeclared variables) and what that logs;
  - that the store renders only prose while code renders the data;
  - the trace link (`langfuse.observation.prompt.name` / `.version` on `chat` spans, `alfred.prompts` listing every version);
  - the edit workflow: edit in Langfuse → `alfred evals run --prompt-label <label>` → move `production` in Langfuse; or edit the repo default → PR → `alfred prompts push --promote` after merge;
  - `alfred prompts pull` to bring UI edits back into the repo.
- [ ] **Step 2: `docs/deployment.md`.** Under the configuration section, list `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `ALFRED_PROMPT_LABEL` and `PROMPT_CACHE_TTL_S`. Explain that:
  - the Alfred container must share a docker network with Langfuse's web service, done through an operator-owned `docker-compose.override.yml`, not the tracked compose file;
  - the first deploy runs `alfred prompts push --promote` from the host (with `LANGFUSE_HOST=http://localhost:3300`) before adding the variables to `.env`;
  - without them, Alfred serves the repo defaults.
- [ ] **Step 3: `docs/evals.md`.** Document `--prompt-label`:
  - the three environment variables it reads;
  - that a Langfuse published only on the host's loopback is not reachable from the eval container, so use an address the container can reach;
  - that the scorecard's commit line names the label;
  - the candidate workflow: push without `--promote`, run with `--prompt-label latest`, compare against a repo-default run.
- [ ] **Step 4: `docs/architecture.md:673`.** Replace the line about `core/conscious/prompts/` with: "**System 2's prose** (personality, voice delivery, identity, location, integrations, proactivity) is served by the prompt store (`shared/prompts/`); the sir and guest identity sections are separate prompts, enforcing the privacy boundary."
- [ ] **Step 5: `shared/CLAUDE.md` and root `CLAUDE.md`.** In `shared/CLAUDE.md`, add one bullet describing `shared/prompts/`: store, defaults, link, CLI. In the root `CLAUDE.md` Key Paths, add `shared/prompts/` (prompt store and repo defaults) and update any path that pointed at `core/conscious/prompts/`.
- [ ] **Step 6: Check, then commit**

Run: `git grep -n "conscious/prompts\|ContextAssembler"`. Expected: matches only in `docs/superpowers/` (historical plans and specs).

```bash
git add docs CLAUDE.md shared/CLAUDE.md
git commit -m "docs: prompts in Langfuse — store, CLI, deployment and evals"
```

---

### Task 10: Live verification (controller, not a subagent)

These steps talk to the self-hosted Langfuse and the local vLLM. They are run by the controller and **posted to #317 as raw output** (the scorecards and the CLI output), not as a summary. Read Langfuse's keys from `~/code/langfuse/.env` into the shell and never echo them. vLLM bound: the eval harness's own concurrency, at most 4 requests in flight, as in PR 2's runs.

- [ ] **Step 1: Round-trip the defaults.**
  1. `uv run --no-sync alfred prompts diff`, expected to exit 1 with every prompt "not in Langfuse at label production".
  2. `uv run --no-sync alfred prompts push`, with no `--promote`. Expected: 12 "pushed v1" lines.
  3. `uv run --no-sync alfred prompts diff --label latest`, expected to exit 0 with "No drift". This proves Langfuse stores the text byte for byte under Ruling 2.

  Production is unaffected: no `production` label exists yet, and prod has no `LANGFUSE_HOST`.
- [ ] **Step 2: Langfuse links a span to a prompt.**
  1. From the scratchpad, export one span named `chat probe` to Langfuse's OTLP HTTP endpoint (`/api/public/otel/v1/traces`, basic auth). It carries `gen_ai.operation.name=chat`, `langfuse.observation.prompt.name=system2-personality` and `langfuse.observation.prompt.version=1`. Use `uv run --with opentelemetry-exporter-otlp-proto-http` if the HTTP exporter is not installed.
  2. Read the observation back with `GET /api/public/observations?traceId=<id>` and confirm its prompt fields name `system2-personality` v1.
  3. If Langfuse ignores the attributes, stop and rule on the attribute names before the PR. The unit test in Task 4 only proves Alfred sets them.
- [ ] **Step 3: Evals, repo defaults versus Langfuse.** Run the suites PR 2 ran (`uv run --no-sync alfred evals list` shows them) twice:
  - once with repo defaults;
  - once with `--prompt-label latest`, where `LANGFUSE_HOST` is an address the eval container can reach (take it from `docker port langfuse-langfuse-web-1`; never write it to a file).

  Expected:
  - The second scorecard's commit line says `prompts: Langfuse label latest`.
  - Pass rates match within run-to-run noise, since the texts are identical.
  - The eval container's log shows no "unreachable" warning.

  Lock goldens are expected to fail as they do on master (#292); that is not a regression.
- [ ] **Step 4:** Run the gate and the secret-hygiene check (the runbook's §Secret hygiene, run from there, never quoted). Then report to the owner and ask before pushing or opening the PR.

## After merge (owner's go only)

Merging deploys. Then, in this order:

1. From the host: `alfred prompts push --promote`, with `LANGFUSE_HOST=http://localhost:3300` and the keys in the shell.
2. Add `LANGFUSE_HOST=http://langfuse-web:3000`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` to `~/code/alfred-deploy/.env`.
3. Restart the stack the way `docs/deployment.md` says.
4. Check the startup log line "Prompts: Langfuse, label production".
5. Send one real turn and confirm its `chat` observation in Langfuse links to `system2-personality` at the promoted version.
6. Post both to #317.

Until step 2, production serves the repo defaults, which are byte-identical to the merged code's prompts except for Ruling 2's whitespace.
