# First-Deploy Replay of alfred:events History

**Feature:** `channels-credentials` consumer group creation at stream id `0` (`core/channels/service_credentials.py::credential_push_worker`)
**Priority:** medium
**Type:** integration

## Prerequisites
- A Redis instance with an EXISTING `alfred:events` stream that already contains one or more `ServiceRegistered` entries from BEFORE the `channels-credentials` consumer group has ever been created (i.e. simulate a fresh deploy of this feature against an already-running Alfred install with registration history)
- Credentials for the relevant service(s) already stored in the OS keyring from a prior session
- A stub or Plan-2 service reachable at its `credentials_endpoint` to observe the replayed push (home-service doesn't declare `credentials_schema` yet on this branch)

## Test Steps
1. Confirm (via `XINFO GROUPS alfred:events`) that no `channels-credentials` group exists yet.
2. Confirm `alfred:events` already has old `ServiceRegistered` entries (from before this deploy) for the stub service.
3. Start `core.channels` for the first time with this feature deployed — this creates the `channels-credentials` group at id `0` per `docs/secrets.md` "First-deploy replay" note.
4. Observe the stub service receiving a credential push shortly after startup WITHOUT any new registration event being published — i.e. the worker is replaying pre-existing history, not just listening for new events.
5. Confirm via logs/`XPENDING` that the entire backlog is processed (ACKed) once, not repeatedly on subsequent restarts of `core.channels` (group's last-delivered-id should have advanced past the backlog after the first run).

## Expected Result
- On first deploy, ALL historical `ServiceRegistered` events on `alfred:events` trigger a credential push (idempotent POST), not just events after the worker starts.
- Subsequent `core.channels` restarts do NOT re-replay the same old backlog (group position persists in Redis).

## Notes
- This is an operational/deployment-ordering detail explicitly called out in `docs/secrets.md` ("Operational notes → First-deploy replay") that has no automated test — it depends on the *actual* state of a pre-existing `alfred:events` stream and consumer-group creation semantics, not something the mocked-Redis test suite exercises.
- Low risk if the push is genuinely idempotent (per design), but worth confirming no duplicate side effects on the receiving service (e.g. double-writing config, restart loops) if it's not perfectly idempotent in practice.
