# Claude Code Config Consolidation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate duplicated Claude Code configuration across `~/code/private` projects by extracting shared conventions into global user-level config.

**Architecture:** Claude Code settings merge additively across scopes (user → project → local). CLAUDE.md files are concatenated. We extract common content to `~/.claude/` (user scope) and trim per-project files to project-specific content only.

**Tech Stack:** Claude Code settings.json, CLAUDE.md, shell hooks

---

### Task 1: Create global `~/.claude/CLAUDE.md`

**Files:**
- Create: `~/.claude/CLAUDE.md`

- [ ] **Step 1: Create the global CLAUDE.md**

```markdown
# Global Development Conventions

Shared conventions applied to all projects. Project-level CLAUDE.md files contain project-specific instructions only.

## Superpowers Skill Guidelines

- Use opus with max effort from brainstorming and planning.
- Use /executing-plans skill for executing the plan.
- Use sonnet or opus for implementing the plan.
- Use opus for reviewing and simplification stages.
- Use haiku for committing.
- Prefer latest internet grounded knowledge over training knowledge.
- Use context7 to check latest docs and for external dependencies.
- Add @feature-dev:code-architect review step as a task for each plan you implement. Fix every issue that comes up during the code architect review step.
- Add /simplify skill step as a task for each plan you implement. Fix every issue that comes up during the simplify step.
- Add claude md skill as a task to improve and revise claude context, memories etc.
- Finally create a PR with the changes.

## Python Tooling (NON-NEGOTIABLE)

- **Package manager:** `uv` (astral) — never use pip or pip-tools directly
- **Linting + formatting:** `ruff` — run `ruff check .` and `ruff format .`
- **Type checking:** `mypy --strict` — all code must pass strict mypy
- **Testing:** `pytest` + `pytest-asyncio`
- **Python:** 3.13+ only

## Python Conventions

- Python 3.13+, use modern syntax (match/case, type unions with |, etc.)
- Async-first: use async/await for all I/O operations
- Pydantic v2 for all data models and schemas
- Type hints on all function signatures
- `ruff` for linting AND formatting (line-length 100)
- pytest + pytest-asyncio for testing
- Use `loguru` for logging (never stdlib logging)
```

- [ ] **Step 2: Verify the file was created**

Run: `cat ~/.claude/CLAUDE.md | head -5`
Expected: Shows the header lines

---

### Task 2: Update global `~/.claude/settings.json` with shared plugins and hooks

**Files:**
- Modify: `~/.claude/settings.json`

- [ ] **Step 1: Add shared plugins and hooks to global settings**

The final file should be:

```json
{
  "enabledPlugins": {
    "github@claude-plugins-official": true,
    "superpowers@claude-plugins-official": true,
    "playwright@claude-plugins-official": true,
    "typescript-lsp@claude-plugins-official": true,
    "claude-md-management@claude-plugins-official": true,
    "serena@claude-plugins-official": true,
    "claude-code-setup@claude-plugins-official": true,
    "swift-lsp@claude-plugins-official": true,
    "frontend-design@claude-plugins-official": true,
    "code-review@claude-plugins-official": true,
    "feature-dev@claude-plugins-official": true,
    "code-simplifier@claude-plugins-official": true,
    "context7@claude-plugins-official": true
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "case \"$CLAUDE_FILE_PATH\" in *.env.example) ;; *.env|*.env.*|*credentials*|*secrets*) echo 'BLOCK: Cannot edit secrets files' >&2; exit 2;; esac"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "case \"$CLAUDE_FILE_PATH\" in *.py) ruff check --fix --quiet \"$CLAUDE_FILE_PATH\" 2>/dev/null; ruff format --quiet \"$CLAUDE_FILE_PATH\" 2>/dev/null;; esac"
          }
        ]
      }
    ]
  }
}
```

Notes:
- Secrets hook uses exit code 2 (blocking error) per Claude Code convention
- `.env.example` is explicitly allowed (matched first, falls through)
- Ruff hook uses bare `ruff` (not `uv run ruff`) since it should be globally installed
- `2>/dev/null` suppresses errors in non-Python projects where ruff isn't available

---

### Task 3: Clean up `alfred/.claude/settings.json` (workspace level)

**Files:**
- Modify: `~/code/private/alfred/.claude/settings.json`

- [ ] **Step 1: Remove plugins now in global settings, keep `ralph-loop` (alfred-only)**

```json
{
  "permissions": {
    "allow": [
      "Bash",
      "Read",
      "Edit",
      "Write",
      "Glob",
      "Grep",
      "WebFetch",
      "WebSearch"
    ]
  },
  "enabledPlugins": {
    "ralph-loop@claude-plugins-official": true
  }
}
```

---

### Task 4: Clean up `alfred/alfred/.claude/settings.json` (monorepo level)

**Files:**
- Modify: `~/code/private/alfred/alfred/.claude/settings.json`
- Delete: `~/code/private/alfred/alfred/.claude/hooks/protect-env.sh`
- Delete: `~/code/private/alfred/alfred/.claude/hooks/ruff-autoformat.sh`

- [ ] **Step 1: Remove secrets + ruff hooks (now global), keep worktree-setup**

```json
{
  "permissions": {
    "allow": [
      "mcp__plugin_playwright_playwright__*"
    ]
  },
  "hooks": {
    "WorktreeCreate": [
      {
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/worktree-setup.sh",
            "statusMessage": "Setting up worktree environment..."
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Delete the now-unused hook scripts**

```bash
rm ~/code/private/alfred/alfred/.claude/hooks/protect-env.sh
rm ~/code/private/alfred/alfred/.claude/hooks/ruff-autoformat.sh
```

Keep `worktree-setup.sh` — it's alfred-specific.

---

### Task 5: Clean up `rekordbox-tagger/.claude/settings.json`

**Files:**
- Modify: `~/code/private/rekordbox-tagger/.claude/settings.json`

- [ ] **Step 1: Remove all plugins and hooks (all now global)**

```json
{
  "permissions": {
    "allow": [
      "Bash",
      "Read",
      "Edit",
      "Write",
      "Glob",
      "Grep",
      "WebFetch",
      "WebSearch"
    ]
  }
}
```

All plugins (frontend-design, code-review, feature-dev, context7) are now global.
Both hooks (secrets protection, ruff format) are now global.

---

### Task 6: Clean up `dj-ledfx/.claude/settings.json`

**Files:**
- Modify: `~/code/private/dj-ledfx/.claude/settings.json`

- [ ] **Step 1: Remove global plugins and hooks, keep prettier hook**

```json
{
  "permissions": {
    "allow": [
      "Bash",
      "Read",
      "Edit",
      "Write",
      "Glob",
      "Grep",
      "WebFetch",
      "WebSearch"
    ]
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "case \"$CLAUDE_FILE_PATH\" in *.ts|*.tsx) cd frontend && npx prettier --write \"../$CLAUDE_FILE_PATH\" 2>/dev/null;; esac"
          }
        ]
      }
    ]
  }
}
```

The `.py` case is removed from the PostToolUse hook (handled globally).
The PreToolUse secrets hook is removed entirely (handled globally).
All plugins removed (all now global).

---

### Task 7: Trim `alfred/CLAUDE.md` (workspace level)

**Files:**
- Modify: `~/code/private/alfred/CLAUDE.md`

- [ ] **Step 1: Remove duplicated Superpowers and Tooling sections**

Remove the "Superpowers Skill Guidelines" and "Tooling (NON-NEGOTIABLE)" sections entirely — they're now in `~/.claude/CLAUDE.md`. Keep everything else (Repos, Container Runtime, Dev Environment Notes, Architecture, Workflow).

---

### Task 8: Trim `rekordbox-tagger/CLAUDE.md`

**Files:**
- Modify: `~/code/private/rekordbox-tagger/CLAUDE.md`

- [ ] **Step 1: Remove duplicated sections and update Python version**

Remove the "Superpowers Skill Guidelines" and "Tooling (NON-NEGOTIABLE)" sections — now global.
Update Python version references from `3.11` to `3.13` in Commands section.
Update `from __future__ import annotations` note in Code Style — not needed in 3.13+, but keep if it's a project convention.
Keep everything else (Commands, CLI, Architecture, Data Protection, Hardware, Gotchas, Frontend).

---

### Task 9: Trim `dj-ledfx/CLAUDE.md`

**Files:**
- Modify: `~/code/private/dj-ledfx/CLAUDE.md`

- [ ] **Step 1: Remove duplicated Superpowers section**

Remove the "Superpowers Skill Guidelines" section — now global.
The Commands section already references `uv`/`ruff`/`mypy` in project-specific ways, so keep it.
Keep everything else (Architecture, Code Style, Key Design Decisions, Gotchas, Testing).
