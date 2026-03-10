#!/usr/bin/env bash
# Tail logs from all Alfred infrastructure containers.
#
# Usage: ./scripts/dev-logs.sh [container-name]

set -euo pipefail

if [ $# -gt 0 ]; then
    container logs "$1" 2>&1
else
    echo "==> Redis logs:"
    container logs redis 2>&1 | tail -5
    echo ""
    echo "==> Mosquitto logs:"
    container logs mosquitto 2>&1 | tail -5
fi
