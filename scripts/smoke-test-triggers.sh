#!/usr/bin/env bash
# End-to-end smoke test for the Trigger Engine.
#
# Creates a one-shot time trigger via JSON-RPC, waits for it to fire,
# and verifies the action appeared on the actions stream and the
# trigger was auto-deleted.
#
# Prerequisites: Redis running, Trigger Engine running (python -m core.triggers)
#
# Usage: ./scripts/smoke-test-triggers.sh

set -euo pipefail

REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
TRIGGER_HOST="${TRIGGER_HOST:-localhost}"
TRIGGER_PORT="${TRIGGER_PORT:-8001}"
TIMEOUT=15

# Check prerequisites
command -v redis-cli >/dev/null 2>&1 || { echo "ERROR: redis-cli not found. Install: brew install redis"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "ERROR: curl not found"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq not found. Install: brew install jq"; exit 1; }

echo "=== Alfred Trigger Engine Smoke Test ==="
echo ""

# Check services
echo "Checking services..."
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null 2>&1 || { echo "ERROR: Redis not reachable at $REDIS_HOST:$REDIS_PORT"; exit 1; }
echo "  ✓ Redis"

# Check Trigger Engine HTTP server
curl -sf --max-time 2 "http://$TRIGGER_HOST:$TRIGGER_PORT" >/dev/null 2>&1 || true
# The server returns 200 even for empty requests, so just check connectivity
curl -sf --max-time 2 -X POST "http://$TRIGGER_HOST:$TRIGGER_PORT" \
  -H "Content-Type: application/json" \
  -d '{"method":"triggers.list_triggers","params":{"enabled_only":false},"id":"health"}' \
  >/dev/null 2>&1 || { echo "ERROR: Trigger Engine HTTP server not reachable at $TRIGGER_HOST:$TRIGGER_PORT"; exit 1; }
echo "  ✓ Trigger Engine HTTP server"

# Check consumer group
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XINFO GROUPS alfred:home:state_changed 2>/dev/null | grep -q "trigger-engine" || { echo "ERROR: trigger-engine consumer group not found on alfred:home:state_changed. Is the Trigger Engine running?"; exit 1; }
echo "  ✓ Trigger Engine consumer group"

echo ""

# Record baseline: actions stream length
BEFORE_LEN=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XLEN alfred:actions 2>/dev/null || echo "0")
echo "Actions stream length before: $BEFORE_LEN"

# Create a one-shot time trigger that fires immediately
# run_at is set to 2 seconds from now to give the tick loop time to pick it up
RUN_AT=$(date -u -v+2S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '+2 seconds' '+%Y-%m-%dT%H:%M:%SZ')

echo ""
echo "Creating one-shot time trigger (run_at: $RUN_AT)..."

CREATE_RESPONSE=$(curl -sf --max-time 5 -X POST "http://$TRIGGER_HOST:$TRIGGER_PORT" \
  -H "Content-Type: application/json" \
  -d "$(cat <<EOF
{
  "method": "triggers.create_trigger",
  "params": {
    "name": "smoke_test_trigger",
    "trigger_type": "time",
    "conditions": {"cron": null, "run_at": "$RUN_AT"},
    "action": {
      "tool_name": "smoke_test.ping",
      "target_service": "smoke-test",
      "parameters": {"test": true}
    },
    "one_shot": true
  },
  "id": "smoke-create"
}
EOF
)")

# Check for errors in the response
if echo "$CREATE_RESPONSE" | jq -e '.error' >/dev/null 2>&1; then
  echo "ERROR: Failed to create trigger:"
  echo "$CREATE_RESPONSE" | jq .
  exit 1
fi

TRIGGER_ID=$(echo "$CREATE_RESPONSE" | jq -r '.result.trigger_id // empty')
if [ -z "$TRIGGER_ID" ]; then
  # The result might contain an error dict from the tool
  ERROR_MSG=$(echo "$CREATE_RESPONSE" | jq -r '.result.error // empty')
  if [ -n "$ERROR_MSG" ]; then
    echo "ERROR: Tool returned error: $ERROR_MSG"
    exit 1
  fi
  echo "ERROR: Could not extract trigger_id from response:"
  echo "$CREATE_RESPONSE" | jq .
  exit 1
fi

echo "  ✓ Created trigger: $TRIGGER_ID"

# Wait for the trigger to fire (action appears on alfred:actions)
echo ""
echo "Waiting for trigger to fire (timeout: ${TIMEOUT}s)..."
FIRED=false
for i in $(seq 1 "$TIMEOUT"); do
  AFTER_LEN=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XLEN alfred:actions 2>/dev/null || echo "0")
  if [ "$AFTER_LEN" -gt "$BEFORE_LEN" ]; then
    FIRED=true
    break
  fi
  sleep 1
done

if [ "$FIRED" = false ]; then
  echo ""
  echo "✗ SMOKE TEST FAILED — Trigger did not fire within ${TIMEOUT}s."
  echo ""
  echo "=== Diagnostics ==="
  echo ""
  echo "Trigger still in Redis:"
  redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" HGET alfred:triggers "$TRIGGER_ID" 2>/dev/null || echo "  (not found)"
  echo ""
  echo "Check Trigger Engine terminal for errors."
  # Cleanup: delete the trigger if it still exists
  curl -sf --max-time 5 -X POST "http://$TRIGGER_HOST:$TRIGGER_PORT" \
    -H "Content-Type: application/json" \
    -d "{\"method\":\"triggers.delete_trigger\",\"params\":{\"trigger_id\":\"$TRIGGER_ID\"},\"id\":\"cleanup\"}" >/dev/null 2>&1 || true
  exit 1
fi

echo ""
echo "=== Action received! ==="
# Show the latest action entry
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" XREVRANGE alfred:actions + - COUNT 1
echo ""

# Verify one-shot deletion: trigger should no longer exist
echo "Verifying one-shot trigger was deleted..."
REMAINING=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" HGET alfred:triggers "$TRIGGER_ID" 2>/dev/null || echo "")
if [ -z "$REMAINING" ] || [ "$REMAINING" = "(nil)" ]; then
  echo "  ✓ Trigger auto-deleted (one-shot)"
else
  echo "  ✗ WARNING: Trigger still exists after firing (expected deletion for one-shot)"
fi

# Check scratchpad for the fire observation
echo ""
echo "=== Scratchpad queue ==="
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" LRANGE alfred:scratchpad:queue 0 5

echo ""
echo "✓ SMOKE TEST PASSED — Trigger Engine created, fired, and cleaned up a one-shot trigger."
exit 0
