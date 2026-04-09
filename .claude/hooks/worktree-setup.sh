#!/bin/bash
# WorktreeCreate hook: symlink .env from main repo into new worktrees
INPUT=$(cat)
WORKTREE_PATH=$(echo "$INPUT" | jq -r '.worktree_path // empty')

if [[ -z "$WORKTREE_PATH" ]]; then
    exit 0
fi

# Find the main worktree (first line of `git worktree list`)
MAIN_REPO=$(git -C "$WORKTREE_PATH" worktree list --porcelain | head -1 | sed 's/^worktree //')

if [[ -z "$MAIN_REPO" ]]; then
    exit 0
fi

# Symlink .env if it exists in the main repo and not already in the worktree
if [[ -f "$MAIN_REPO/.env" && ! -e "$WORKTREE_PATH/.env" ]]; then
    ln -s "$MAIN_REPO/.env" "$WORKTREE_PATH/.env"
    echo "{\"systemMessage\": \"Symlinked .env from main repo into worktree\"}"
fi

exit 0
