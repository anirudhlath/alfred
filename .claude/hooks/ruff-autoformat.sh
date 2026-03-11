#!/bin/bash
# PostToolUse hook: auto-format + lint Python files after Edit/Write
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only process Python files
if [[ -z "$FILE_PATH" ]] || [[ "$FILE_PATH" != *.py ]]; then
    exit 0
fi

# Only process if file exists (Write might create, Edit modifies)
if [[ ! -f "$FILE_PATH" ]]; then
    exit 0
fi

ruff check --fix --quiet "$FILE_PATH" 2>/dev/null
ruff format --quiet "$FILE_PATH" 2>/dev/null
exit 0
