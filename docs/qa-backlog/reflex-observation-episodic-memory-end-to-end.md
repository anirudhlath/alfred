# Reflex Observation Written to Episodic Memory End-to-End

**Feature:** D8 — Memory Ingestor (ReflexObservation → EpisodicMemory pipeline)
**Priority:** critical
**Type:** e2e

## Prerequisites
- Alfred unified runner started (`python -m runner`)
- Redis Stack running and accessible
- Ollama running with a model loaded (for Reflex Engine to fire actions)
- Home Assistant MQTT bridge active with at least one entity that triggers a reflex rule
- At least one trigger rule configured in the trigger registry

## Test Steps
1. Start the full Alfred stack (Redis Stack + Mosquitto via Homebrew, or `uv run alfredctl up`) and confirm all services report healthy in logs
2. Trigger a state change event in Home Assistant that matches a known reflex rule (e.g., motion sensor activation)
3. Confirm in the Reflex Runner logs that a `ReflexObservation` was published to `alfred:reflex:observations` (look for `"Published observation"` log line)
4. Confirm in the Memory Ingestor logs that the observation was consumed and written (look for `"Ingested reflex observation"` log line)
5. Open a Redis CLI (`redis-cli`) and query the episodic hot store: `FT.SEARCH idx:context "@source:{reflex}" LIMIT 0 5`
6. Verify the returned document contains the correct `summary`, `source=reflex`, and a non-zero `significance.overall` value
7. Trigger 3 more distinct state changes through the same rule (wait a few seconds between each)
8. Verify all 4 observations appear as separate episodic entries in Redis

## Expected Result
- Each reflex action produces exactly one episodic entry in the hot Redis vector store
- The `summary` field follows the `[reflex:<origin>] <tool_name>(<params>) → <status>` format
- The `source` field is `"reflex"` on all entries
- The `semantic_key` field is present and non-empty
- No duplicate entries appear for a single state change event
- Memory Ingestor does not crash or log errors during normal operation

## Notes
- The Memory Ingestor consumer group is `memory-ingestor` on stream `alfred:reflex:observations` — confirm with `XINFO GROUPS alfred:reflex:observations`
- If Ollama is down, the Reflex Engine will not fire and no observation will be published; this is expected behaviour
- The significance scorer runs an async embedding operation — verify the entry has `significance.overall > 0.0` and not just the placeholder `0.0`
- Edge case: trigger a state change when Memory Ingestor is not running, then start it — entries should be replayed from the stream (consumer group offset=0)
