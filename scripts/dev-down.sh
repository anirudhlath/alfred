#!/usr/bin/env bash
# Stop Alfred infrastructure services (macOS Homebrew).
#
# Usage: ./scripts/dev-down.sh

set -euo pipefail

echo "==> Stopping Redis..."
brew services stop redis 2>/dev/null || true

echo "==> Stopping Mosquitto..."
brew services stop mosquitto 2>/dev/null || true

echo "Done."
