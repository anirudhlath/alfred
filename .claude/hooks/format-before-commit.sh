#!/usr/bin/env bash
# PreToolUse hook: when the Bash tool is about to run a `git commit`, format first so
# agent commits arrive formatted (CI is check-only by policy — no auto-fix-push bots).
# Limitation: files staged in an *earlier* command may miss late formatting; agents
# normally `git add && git commit` in one command, and CI still backstops.
set -uo pipefail
cmd=$(jq -r '.tool_input.command // empty' 2>/dev/null)
case "$cmd" in
  *"git commit"*)
    cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 0
    uv run ruff check --fix . >/dev/null 2>&1
    uv run ruff format . >/dev/null 2>&1
    ;;
esac
exit 0
