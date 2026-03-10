#!/usr/bin/env bash
# Start Alfred infrastructure in Apple containers (macOS 26+).
# Python services (bridge, reflex, home-service) run natively for dev.
#
# Usage: ./scripts/dev-up.sh

set -euo pipefail

NETWORK="alfred-net"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Starting Apple container runtime..."
container system start 2>/dev/null || true

# Wait for system to be ready
for i in $(seq 1 10); do
    if container system status &>/dev/null; then
        break
    fi
    echo "    Waiting for container runtime... ($i/10)"
    sleep 2
done

echo "==> Creating network '$NETWORK'..."
container network create "$NETWORK" 2>/dev/null || echo "    Network already exists"

echo "==> Starting Redis..."
if ! container inspect redis &>/dev/null; then
    container run -d \
        --name redis \
        --network "$NETWORK" \
        -p 6379:6379 \
        redis:7-alpine
else
    container start redis 2>/dev/null || echo "    Redis already running"
fi

echo "==> Starting Mosquitto..."
if ! container inspect mosquitto &>/dev/null; then
    container run -d \
        --name mosquitto \
        --network "$NETWORK" \
        -p 1883:1883 \
        -v "$PROJECT_DIR/infra/mosquitto.conf:/mosquitto/config/mosquitto.conf" \
        eclipse-mosquitto:2
else
    container start mosquitto 2>/dev/null || echo "    Mosquitto already running"
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
