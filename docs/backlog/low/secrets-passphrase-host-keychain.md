# Store the Generated Secrets Passphrase in the Host Keychain

## Summary

`alfredctl up --mode persistent` generates a random `ALFRED_SECRETS_PASSPHRASE` on first
run and persists it as a **plaintext file** at `<persist-dir>/.secrets-passphrase`
(mode `0600`) so the `cryptfile` keyring backend can decrypt stored credentials across
container restarts without the user managing a passphrase by hand
(`alfredctl/main.py:_passphrase()`). This is a reasonable first cut — filesystem
permissions plus the fact that anyone with read access to `<persist-dir>` already has
read access to the encrypted keyring file it unlocks — but it's not as strong as storing
the passphrase in the host's own OS keychain (macOS Keychain / Linux Secret Service),
which is exactly the protection `shared/secrets.py`'s `native` backend already provides
*outside* a container.

## Context / Motivation

- The plaintext-file approach means the passphrase's confidentiality reduces entirely to
  filesystem permissions on the host — fine for a single-user dev/self-hosted box, weaker
  than necessary if the persist directory is ever backed up, synced, or shared.
- `keyring`/`keyrings.cryptfile` are already dependencies; the host side (outside the
  container) could use the `keyring` library's native backend to store just the
  passphrase itself, then have `alfredctl` read it from there instead of the flat file
  before injecting it as `ALFRED_SECRETS_PASSPHRASE` into the container's environment.
- This only applies to `alfredctl up`'s convenience path — a user who sets
  `ALFRED_SECRETS_PASSPHRASE` in their own environment (e.g. from their own password
  manager) already bypasses the generated-file mechanism entirely
  (`_passphrase()`'s env-wins precedence).

## Acceptance Criteria

- [ ] `alfredctl up --mode persistent` stores the generated passphrase in the host's OS
      keychain (via `keyring.set_password`) instead of (or in addition to, for a
      migration window) `<persist-dir>/.secrets-passphrase`.
- [ ] Existing persistent deployments with an already-generated plaintext passphrase file
      keep working — either a one-time migration on next `alfredctl up`, or documented
      manual migration steps.
- [ ] Falls back sanely on hosts with no OS keychain available (bare Linux without
      Secret Service, CI) — don't hard-fail `alfredctl up` where the flat-file fallback
      currently works.
- [ ] `docs/containerization.md` §6 updated to describe the new storage location.
