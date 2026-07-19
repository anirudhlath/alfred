# Runtime state persists under ALFRED_DATA_DIR across a restart

**Feature:** State consolidation (containerization Part 1)
**Priority:** high
**Type:** integration

## Prerequisites
- Alfred running natively (Redis Stack + Mosquitto reachable; `uv run python -m runner`)
- A clean `ALFRED_DATA_DIR` (default `./data`) — or set it to a scratch dir for the test

## Test Steps
1. Start the runner with `ALFRED_DATA_DIR` unset (default `./data`).
2. Exercise each writable-state path:
   - Create a routine (via the conscious engine / a proactive suggestion) → expect a YAML file in `data/routines/`.
   - Create a trigger/reminder → expect a YAML snapshot in `data/triggers/`.
   - Register a WebAuthn passkey → expect `data/credentials.db`.
   - Let the Reflex engine produce observations → expect the Memory Ingestor to write `data/episodic_cold.db` (cold store) and the scratchpad to `data/scratchpad.md`.
3. Confirm NOTHING is written under the installed package dirs (`core/memory/scratchpad.md`, `core/memory/episodic_cold.db`, `core/memory/routines/*.yaml`, `core/memory/triggers/`).
4. Stop the runner; restart it.
5. Confirm the routines, triggers, credentials, and episodic cold memories are still present and loaded (not reset).
6. Re-run with a *different* `ALFRED_DATA_DIR=/tmp/alfred-test` and confirm a fresh, isolated state dir is created there and the default `./data` is untouched.

## Expected Result
- All writable state lands under `ALFRED_DATA_DIR` (steps 2–3); the package tree stays read-only.
- State survives a restart (step 5).
- Switching `ALFRED_DATA_DIR` yields a fully isolated state tree (step 6) — this is the basis for per-worktree isolation in Part 2.

## Notes
- Automated coverage exists (`tests/core/memory/test_no_package_state_paths.py` gate + per-path wiring tests, 1129 passing), but the end-to-end "real services write to data/ and reload after restart" path is worth one manual pass.
- Known Part 1 limitation (not a bug): existing pre-consolidation dev state (`core/memory/scratchpad.md`, `core/memory/episodic_cold.db`) is NOT auto-migrated into `data/` — a dev upgrading on this branch starts those stores empty (graceful degradation). Fresh installs are unaffected.
