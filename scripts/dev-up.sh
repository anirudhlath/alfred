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
echo "  Terminal 1: cd ../home-service && uv run uvicorn app.server:app --port 8000"
echo "  Terminal 2: uv run python -m runner      # starts bridge + reflex + triggers"
