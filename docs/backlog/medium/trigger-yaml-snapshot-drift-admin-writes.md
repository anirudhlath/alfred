# Trigger YAML Snapshot Drift on Admin Writes

## Summary

Admin trigger mutations (fire one-shot HDEL, last_fired hset, enabled hset) write
Redis directly and bypass `TriggerStore`'s YAML snapshot. On a cold start with an
empty `alfred:triggers` hash, `load()` rehydrates from YAML — so fired one-shot
triggers resurrect, and `last_fired`/`enabled` changes roll back.

## Context / Motivation

The channels process deliberately avoids importing `TriggerRegistry` or `BaseTrigger`
subtypes (plan constraint: channels must not depend on trigger domain internals). As a
result, `admin_api.py` cannot construct `BaseTrigger` models needed by
`TriggerStore.save()` / `TriggerStore.delete()`, and writes only to the Redis hash
(`alfred:triggers`).

`TriggerStore` (`core/triggers/store.py`) maintains a parallel YAML snapshot
(`core/memory/triggers/`) for durability across Redis restarts. When Redis is cold
and the hash is empty, `load()` rehydrates from YAML, silently undoing any admin
mutations that happened since the last snapshot.

This was a deliberate trade-off: the admin surface is low-frequency and non-critical.
The drift only manifests on a full Redis cold start, which is rare in production.

## Acceptance Criteria

- Admin fire/enable mutations keep the YAML snapshot consistent with the Redis hash
  (or snapshot rehydration logic tolerates known-safe drift such as `last_fired`
  being stale), **and** no `TriggerRegistry`/`BaseTrigger` imports appear in the
  channels process.
- Preferred long-term fix: route admin trigger mutations (fire, enable/disable)
  through `ACTIONS_STREAM` to the triggers process, which owns `TriggerStore` and
  can call `save()` / `delete()` with full type information. This matches the
  original spec's "Publish to ACTIONS_STREAM" mechanism for trigger mutations.
- Alternative: add a lightweight `TriggerStore.patch_redis_only()` helper that
  updates the hash AND YAML in a type-agnostic way (dict-level patch), callable
  from channels without importing trigger types.
