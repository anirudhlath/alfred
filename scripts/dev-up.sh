#!/usr/bin/env bash
# Start Alfred infrastructure for local development (macOS).
# Uses Homebrew services for Redis and Mosquitto.
# Python services (bridge, reflex, home-service) run natively.
#
# Usage: ./scripts/dev-up.sh

set -euo pipefail

# Check Homebrew packages are installed
for pkg in mosquitto; do
    if ! brew list "$pkg" &>/dev/null; then
        echo "Installing $pkg..."
        brew install "$pkg"
    fi
done

echo "==> Starting Redis Stack (with RediSearch)..."
# Homebrew no longer ships a redis-stack formula/service — use the
# redis-stack-server binary (cask) directly.
if redis-cli ping &>/dev/null; then
    echo "    Redis already running"
else
    if ! command -v redis-stack-server &>/dev/null; then
        echo "    Installing redis-stack-server (cask)..."
        # Stop vanilla redis if running — it lacks RediSearch
        brew services stop redis 2>/dev/null || true
        brew tap redis-stack/redis-stack 2>/dev/null || true
        brew install --cask redis-stack-server
    fi
    redis-stack-server --daemonize yes
    sleep 1
fi
# Verify server + RediSearch module (vector search requires it)
if redis-cli ping &>/dev/null && redis-cli MODULE LIST 2>/dev/null | grep -qi search; then
    echo "    Redis Stack: localhost:6379 (RediSearch loaded)"
else
    echo "    ERROR: Redis Stack failed to start or RediSearch module missing"
    echo "    (vanilla redis won't work — Alfred needs redis-stack-server)"
    exit 1
fi

echo "==> Starting Mosquitto..."
# Mosquitto 2.x requires explicit listener + allow_anonymous for dev
MOSQUITTO_CONF="$(brew --prefix)/etc/mosquitto/mosquitto.conf"
if [ ! -f "$MOSQUITTO_CONF" ] || ! grep -q "^listener" "$MOSQUITTO_CONF" 2>/dev/null; then
    echo "    Configuring Mosquitto for dev (listener 1883, anonymous allowed)..."
    mkdir -p "$(brew --prefix)/var/mosquitto"
    printf 'listener 1883\nallow_anonymous true\n' > "$MOSQUITTO_CONF"
fi
brew services start mosquitto 2>/dev/null || echo "    Mosquitto already running"
# Give it a moment to bind
sleep 1
if mosquitto_pub -t "test/ping" -m "ping" 2>/dev/null; then
    echo "    Mosquitto: localhost:1883"
else
    echo "    Restarting Mosquitto..."
    brew services restart mosquitto
    sleep 1
    if mosquitto_pub -t "test/ping" -m "ping" 2>/dev/null; then
        echo "    Mosquitto: localhost:1883"
    else
        echo "    ERROR: Mosquitto failed to start. Check: brew services info mosquitto"
        exit 1
    fi
fi

echo ""
echo "Infrastructure running:"
echo "  Redis:     localhost:6379"
echo "  Mosquitto: localhost:1883"
echo ""
echo "Now start the services:"
echo "  Terminal 1: cd ../home-service && .venv/bin/uvicorn app.server:app --port 8000"
echo "              # (alfred-sdk is not on PyPI — 'uv run' re-resolves and fails;"
echo "              #  use the repo's existing .venv)"
echo "  Terminal 2: uv run python -m runner      # starts all six core services"
echo ""
echo "Home Assistant (:8123) is OPTIONAL — without it, home actions fail fast"
echo "with structured error results; the event pipeline still works end-to-end."
