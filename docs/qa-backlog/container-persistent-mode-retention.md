# Persistent Mode: State Survives `down` / `up`

**Feature:** Containerization — `ALFRED_DATA_MODE=persistent`
**Priority:** critical
**Type:** regression

## Prerequisites

- A container runtime (Docker, Apple `container`, or Podman) with `alfredctl` working.
- `alfred-home-service` cloned as a sibling directory.
- A clean `<repo>/data` directory (or pick an explicit `--persist PATH`) so this test
  isn't polluted by leftover state from prior runs.

## Test Steps

1. `uv run alfredctl up --mode persistent` (default mode — omitting `--mode` is
   equivalent). Confirm the container starts and `<repo>/data/.secrets-passphrase` is
   created (mode `0600`) if it didn't already exist.
2. Through the SPA: complete WebAuthn passkey registration (creates a row in
   `data/credentials.db`).
3. Create at least one durable piece of state that should survive a restart:
   - Send a chat message that results in a stored memory (episodic and/or a learned
     preference), and/or
   - Create a trigger via conversation (routine/reminder), and/or
   - Set an integration credential via Settings (stored in the `cryptfile` keyring at
     `data/secrets/keyring.cfg`).
4. `uv run alfredctl down` — confirm the container is removed but note
   `<repo>/data/` still exists on the host (bind mount, not a container-internal path).
5. `uv run alfredctl up --mode persistent` again (same `--persist` path, no `--no-build`
   needed unless iterating quickly).
6. Confirm the same `.secrets-passphrase` value is reused (not regenerated) — check
   `alfredctl shell` → `env | grep ALFRED_SECRETS_PASSPHRASE` matches the file contents
   from step 1, or simply confirm credential-gated features (Settings integration cards)
   still show the credential as configured without re-entering it.
7. Confirm WebAuthn login still works with the passkey registered in step 2 (proves
   `data/credentials.db` persisted and the same keyring passphrase still decrypts
   anything credential-related).
8. Confirm the memory/trigger created in step 3 is still present (recall it via chat, or
   check the trigger list in the SPA/admin view).
9. Confirm Redis's own persistence survived: `alfredctl shell` → `redis-cli` → check the
   relevant key(s) exist without having been re-seeded from scratch (e.g. `alfred:triggers`
   hash has the trigger from step 3, not empty).

## Expected Result

- All state created before `down` is present after the subsequent `up`: WebAuthn
  credential, memory/preference/trigger, integration credential, and the reused (not
  regenerated) secrets passphrase.
- No re-onboarding is required on the second `up` — the SPA should land the user in an
  already-authenticated state (subject to the auth session's own 24hr TTL, which is
  independent of this test).

## Notes

- This is the regression counterpart to `ephemeral`/`seed` mode, which are *expected* to
  discard everything — don't confuse a passing `ephemeral` teardown with a persistence
  bug; only `persistent` mode is under test here.
- If using a custom `--persist PATH` instead of the default `<repo>/data`, repeat step 5
  with the *same* path — a different path is a fresh, empty data dir by design.
- Delete this file once verified.
