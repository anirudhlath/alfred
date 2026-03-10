#!/usr/bin/env bash
# Stop and remove Alfred infrastructure containers.
#
# Usage: ./scripts/dev-down.sh

set -euo pipefail

echo "==> Stopping Alfred containers..."
for name in redis mosquitto; do
    if container inspect "$name" &>/dev/null; then
        container stop "$name" 2>/dev/null || true
        container rm "$name" 2>/dev/null || true
        echo "    Removed $name"
    fi
done

echo "==> Removing network..."
container network rm alfred-net 2>/dev/null || true

echo "Done."
