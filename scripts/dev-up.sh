#!/usr/bin/env bash
# Start Alfred infrastructure for local development (macOS).
# Uses Homebrew services for Redis and Mosquitto.
# Python services (bridge, reflex, home-service) run natively.
#
# Usage: ./scripts/dev-up.sh

set -euo pipefail

# Check Homebrew packages are installed
for pkg in redis mosquitto; do
    if ! brew list "$pkg" &>/dev/null; then
        echo "Installing $pkg..."
        brew install "$pkg"
    fi
done

echo "==> Starting Redis..."
brew services start redis 2>/dev/null || echo "    Redis already running"
# Verify
if redis-cli ping &>/dev/null; then
    echo "    Redis: localhost:6379"
else
    echo "    ERROR: Redis failed to start"
    exit 1
fi

echo "==> Starting Mosquitto..."
brew services start mosquitto 2>/dev/null || echo "    Mosquitto already running"
# Give it a moment to bind
sleep 1
if mosquitto_pub -t "test/ping" -m "ping" 2>/dev/null; then
    echo "    Mosquitto: localhost:1883"
else
    echo "    ERROR: Mosquitto failed to start"
    exit 1
fi

echo ""
echo "Infrastructure running:"
echo "  Redis:     localhost:6379"
echo "  Mosquitto: localhost:1883"
echo ""
echo "Now run the Python services natively:"
echo "  Terminal 1: python -m bus                # Bridge"
echo "  Terminal 2: python -m core.reflex        # Reflex Runner"
echo "  Terminal 3: cd ../home-service && uvicorn app.server:app --port 8000  # home-service"
