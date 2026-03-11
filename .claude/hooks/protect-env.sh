#!/bin/bash
# PreToolUse hook: block access to .env files containing secrets
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# For Bash tool, check the command string instead
if [[ -z "$FILE_PATH" ]]; then
    exit 0
fi

# Block .env and .env.* files (but allow .env.example)
if [[ "$FILE_PATH" == */.env ]] || [[ "$FILE_PATH" == */.env.local ]] || [[ "$FILE_PATH" == */.env.production ]]; then
    echo "BLOCKED: $FILE_PATH contains secrets — use .env.example instead" >&2
    exit 2
fi

exit 0
