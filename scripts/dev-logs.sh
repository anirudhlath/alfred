#!/usr/bin/env bash
# Show status and logs for Alfred infrastructure services (macOS Homebrew).
#
# Usage: ./scripts/dev-logs.sh [redis|mosquitto]

set -euo pipefail

if [ $# -gt 0 ]; then
    case "$1" in
        redis)
            echo "==> Redis status:"
            brew services info redis
            echo ""
            echo "==> Redis log:"
            tail -20 "$(brew --prefix)/var/log/redis.log" 2>/dev/null || echo "    (no log file found)"
            ;;
        mosquitto)
            echo "==> Mosquitto status:"
            brew services info mosquitto
            echo ""
            echo "==> Mosquitto log:"
            tail -20 "$(brew --prefix)/var/log/mosquitto.log" 2>/dev/null || echo "    (no log file found)"
            ;;
        *)
            echo "Unknown service: $1 (use 'redis' or 'mosquitto')"
            exit 1
            ;;
    esac
else
    echo "==> Service status:"
    brew services info redis
    brew services info mosquitto
fi
