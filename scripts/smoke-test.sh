#!/usr/bin/env bash
# End-to-end smoke test for Alfred Phase 1.
#
# Publishes a fake state_changed event to MQTT, waits for the
# Reflex Engine to process it, and checks for an action result
# on the Redis action_results stream.
#
# Prerequisites: all services running (`uv run alfredctl up` container, or Redis Stack +
# Mosquitto + `uv run python -m runner` natively)
#
# Usage: ./scripts/smoke-test.sh

set -euo pipefail

MQTT_HOST="${MQTT_HOST:-localhost}"
MQTT_PORT="${MQTT_PORT:-1883}"
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
TIMEOUT=30

# The variable named $1, trimmed: a blank value means its default, as in shared/config.py.
setting() {
    local value="${!1:-}"
    value="${value#"${value%%[![:space:]]*}"}"
    printf '%s' "${value%"${value##*[![:space:]]}"}"
}

# Reflex's OpenAI-compatible server (vLLM, LM Studio), resolved as resolve_llm_settings
# (shared/config.py) resolves it: VLLM_BASE_URL; else OPENAI_BASE_URL when SYSTEM2_MODEL is
# blank and the legacy CLAUDE_MODEL is an openai/ string; else OPENAI_COMPAT_HOST; else
# LMSTUDIO_HOST; else the runtime default. Trailing slashes go and /v1 is appended if missing.
LLM_BASE="$(setting VLLM_BASE_URL)"
if [ -z "$LLM_BASE" ] && [ -z "$(setting SYSTEM2_MODEL)" ] && [[ "$(setting CLAUDE_MODEL)" == openai/* ]]; then
    LLM_BASE="$(setting OPENAI_BASE_URL)"
fi
LLM_BASE="${LLM_BASE:-$(setting OPENAI_COMPAT_HOST)}"
LLM_BASE="${LLM_BASE:-$(setting LMSTUDIO_HOST)}"
LLM_BASE="${LLM_BASE:-http://localhost:1234}"
while [[ "$LLM_BASE" == */ ]]; do LLM_BASE="${LLM_BASE%/}"; done
[[ "$LLM_BASE" == */v1 ]] || LLM_BASE="$LLM_BASE/v1"

# Reflex's model, read the same way: REFLEX_MODEL, else vllm:<OPENAI_COMPAT_MODEL>, else none
# (Reflex stays off).
REFLEX="$(setting REFLEX_MODEL)"
if [ -z "$REFLEX" ] && [ -n "$(setting OPENAI_COMPAT_MODEL)" ]; then
    REFLEX="vllm:$(setting OPENAI_COMPAT_MODEL)"
fi
REFLEX_OFF=""
[ -n "$REFLEX" ] || REFLEX_OFF=" Without REFLEX_MODEL or OPENAI_COMPAT_MODEL, Reflex stays off and creates none."

# Check prerequisites
command -v mosquitto_pub >/dev/null 2>&1 || { echo "ERROR: mosquitto_pub not found. Install: brew install mosquitto"; exit 1; }
command -v redis-cli >/dev/null 2>&1 || { echo "ERROR: redis-cli not found. Install: brew install redis"; exit 1; }

echo "=== Alfred Phase 1 Smoke Test ==="
echo ""

# Check services
echo "Checking services..."
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null 2>&1 || { echo "ERROR: Redis not reachable at $REDIS_HOST:$REDIS_PORT"; exit 1; }
echo "  ✓ Redis"

mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "test/ping" -m "ping" 2>/dev/null || { echo "ERROR: Mosquitto not reachable at $MQTT_HOST:$MQTT_PORT"; exit 1; }
echo "  ✓ Mosquitto"

# Verify Bridge is running by checking its consumer group exists on the stream
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XINFO GROUPS alfred:home:state_changed 2>/dev/null | grep -q "reflex-engine" || { echo "ERROR: Bridge/Reflex Runner consumer group not found. Is the Reflex Runner running?$REFLEX_OFF"; exit 1; }
echo "  ✓ Reflex Runner (consumer group active)"

curl -sf http://localhost:8000/health >/dev/null 2>&1 || { echo "ERROR: home-service not reachable at localhost:8000"; exit 1; }
echo "  ✓ home-service"

# Only a vllm: model is served by the local server (an openrouter: one is not), as in
# alfredctl doctor.
if [[ "$REFLEX" == vllm:* ]]; then
    curl -sf "$LLM_BASE/models" >/dev/null 2>&1 || { echo "ERROR: Reflex's model server not reachable at $LLM_BASE"; exit 1; }
    echo "  ✓ Model server"
else
    echo "  - Model server not checked: Reflex's model is not a vllm: one"
fi

echo ""

# Get current length of action_results stream (to detect new entries)
BEFORE_LEN=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XLEN alfred:home:action_results 2>/dev/null || echo "0")
echo "Action results stream length before: $BEFORE_LEN"

# Publish test event: TV turns on
echo ""
echo "Publishing test event: Living Room TV turned ON..."
EVENT_JSON='{"source":"home-service","domain":"home","entity_id":"media_player.living_room_tv","old_state":"off","new_state":"on","attributes":{"friendly_name":"Living Room TV"}}'
mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "home/state_changed" -m "$EVENT_JSON"

# Wait for action result
echo "Waiting for Reflex Engine to process (timeout: ${TIMEOUT}s)..."
for i in $(seq 1 "$TIMEOUT"); do
    AFTER_LEN=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XLEN alfred:home:action_results 2>/dev/null || echo "0")
    if [ "$AFTER_LEN" -gt "$BEFORE_LEN" ]; then
        echo ""
        echo "=== Action result received! ==="
        # Read the latest entry
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XREVRANGE alfred:home:action_results + - COUNT 1
        echo ""
        echo "=== Scratchpad queue ==="
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" LRANGE alfred:scratchpad:queue 0 5
        echo ""
        echo "✓ SMOKE TEST PASSED — Alfred processed the event and produced an action."
        exit 0
    fi
    sleep 1
done

echo ""
echo "✗ SMOKE TEST FAILED — No action result within ${TIMEOUT}s."
echo ""
echo "=== Diagnostics ==="
echo ""
echo "Events on state_changed stream:"
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XRANGE alfred:home:state_changed - + COUNT 3 2>/dev/null || echo "  (stream empty or unreachable)"
echo ""
echo "Pending messages (unprocessed by reflex-engine):"
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XPENDING alfred:home:state_changed reflex-engine 2>/dev/null || echo "  (no pending info)"
echo ""
echo "Check Reflex Runner terminal for errors (model server connectivity, parse failures, etc.)"
exit 1
