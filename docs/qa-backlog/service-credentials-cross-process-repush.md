# Cross-Process Credential Re-Push on Service Restart

**Feature:** `credential_push_worker` (`core/channels/service_credentials.py`) consuming `ServiceRegistered` from `alfred:events`
**Priority:** critical
**Type:** integration

## Prerequisites
- Real Redis (redis-stack, not fakeredis) running via `scripts/dev-up.sh`
- Alfred `core.channels` process running for real (not under pytest) so the real `credential_push_worker` consumer loop is active with consumer group `channels-credentials`
- A stub or Plan-2 service with a `credentials_schema` + `credentials_endpoint` that logs/exposes incoming POST bodies so a re-push is observable (home-service does not declare `credentials_schema` on this branch — use a small stub `AlfredClient`-based service, or Plan 2's home-service once merged)

## Test Steps
1. Start the stub service and let it call `AlfredClient.register()` (publishes `ServiceRegistered` to `alfred:events` after the registry `hset`).
2. `PUT /api/integrations/{service_name}/credentials` with valid values — confirm the stub service receives the initial POST to its `credentials_endpoint`.
3. Kill the stub service process (simulating a crash/restart) WITHOUT clearing its in-memory credentials assumption — confirm it forgets credentials (per design, services hold credentials in memory only).
4. Restart the stub service so it re-registers (`register()` fires again, publishing a new `ServiceRegistered`).
5. Confirm — without any manual re-entry via the UI — that `core.channels`'s `credential_push_worker` consumes the new event and re-POSTs the stored keyring credentials to the service's `credentials_endpoint` within one registration cycle.
6. Check `core.channels` logs for the `"Re-pushed credentials to '{}' at {}"` info line, and confirm no duplicate/missed processing (verify via `XPENDING`/`XACK` on the `channels-credentials` group if inspecting Redis directly).
7. Repeat with the stub service's `credentials_endpoint` intentionally broken during restart — confirm the worker logs a warning (`"Credential push to '{}' failed"`) and ACKs the entry rather than blocking or retrying, per the "no retry loop" design (next registration is the retry vehicle).

## Expected Result
- A service restart alone (no user action) causes credentials to reappear at the service within one re-registration cycle — end-to-end self-healing.
- Failed pushes are logged and ACKed, never crash the worker or the channels process, and don't block subsequent events.

## Notes
- `tests/core/channels/test_service_credentials.py` covers `_handle_event_entry`/`push_credentials` logic with mocked Redis/httpx — this is the first true multi-process, real-Redis-stream, real-restart verification of the self-healing design's core promise.
- Also worth confirming behavior when Redis itself restarts mid-flow (consumer group survives, no double-push) since that's adjacent but out of scope for a dedicated case unless it fails here.
