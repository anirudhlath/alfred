# Sensor Triggers Fire on Real Home Assistant State Changes

**Feature:** Trigger Engine — event consumption
**Priority:** critical
**Type:** integration

## Prerequisites
- Alfred runner (reflex, conscious, channels, triggers, memory-ingestor services) running
- Redis Stack running (redis-stack-server via Homebrew services)
- Home Assistant dev instance (e.g., `home-assistant/` repo) running on localhost:8123
- At least one motion sensor or binary sensor entity in HA (e.g., `binary_sensor.hallway_motion`)
- At least one event-based trigger created in Alfred for the sensor (e.g., "when hallway motion turns on, send notification")

## Test Steps
1. Start the full Alfred stack via runner (should see warmup logs for each service)
2. In Home Assistant UI, manually toggle a sensor state (e.g., simulate motion by toggling `binary_sensor.hallway_motion` from off → on)
3. Watch the Redis stream via CLI: `redis-cli -n 0 XLEN alfred:home:state_changed` (should increment)
4. Observe the triggers process logs for `evaluate_event` calls with the StateChangedEvent
5. Verify the associated trigger fires and produces the expected output (e.g., notification sent, action logged)

## Expected Result
- HA state change publishes a `StateChangedEvent` to `alfred:home:state_changed` stream
- Triggers process consumes from `alfred:home:state_changed` (NOT `alfred:events`)
- Trigger engine evaluates the event and fires the rule if matched
- Notification or action is dispatched via the conscious engine
- Logs show: `"Event evaluation..."` and `"[TriggerEngine]..."` without errors

## Notes
- Before this fix, the triggers process was consuming from `alfred:events` (only trigger engine's own events), so sensor changes never reached the evaluator
- HOME_STATE_STREAM is populated by the MQTT bridge (core/mqtt/ integration with HA)
- Edge case: if no triggers match the state change, evaluation succeeds silently (no action fired is expected behavior)
