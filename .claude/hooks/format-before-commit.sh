#!/usr/bin/env bash
# PreToolUse hook: when the Bash tool is about to run a `git commit`, format first so
# agent commits arrive formatted (CI is check-only by policy — no auto-fix-push bots).
# Limitation: files staged in an *earlier* command may miss late formatting; agents
# normally `git add && git commit` in one command, and CI still backstops.
#
# It runs the ruff uv.lock pins, the one CI runs. `uv run ruff` used to fall through to
# whatever ruff was on PATH in a worktree whose venv lacked the dev extra, and a newer
# ruff also formats the Python blocks inside Markdown, so commits swept in dozens of
# reformatted docs.
set -uo pipefail
cmd=$(jq -r '.tool_input.command // empty' 2>/dev/null)
case "$cmd" in
  *"git commit"*)
    cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 0
    version=$(awk '$0 == "name = \"ruff\"" { getline; gsub(/[^0-9.]/, ""); print; exit }' uv.lock 2>/dev/null)
    [ -n "$version" ] || exit 0
    uvx --quiet "ruff@$version" check --fix . >/dev/null 2>&1
    uvx --quiet "ruff@$version" format . >/dev/null 2>&1
    ;;
esac
exit 0
