# Unified Data Directory (one configurable root for all mutable state)

## Summary

Alfred's mutable state is pinned inside the source tree: episodic cold store,
memory preferences/profile/routines, scratchpad, trigger YAML snapshots, and
Piper voice models all live under `core/…`. Only WebAuthn's `credentials.db`
honors `ALFRED_DATA_DIR`. Consolidate everything under one configurable root.
From the 2026-07-18 configurability audit.

## Context / Motivation

State-in-package-tree breaks containerized/read-only installs, makes backups
a scavenger hunt, and surprises every self-hoster. Current pins:

- Cold store `core/memory/episodic_cold.db` (`core/conscious/__main__.py:164`,
  `core/librarian/__main__.py:48`, `core/memory/ingestor_main.py:50`,
  `core/channels/admin_api.py:106` — four call sites building the same path)
- Preferences/profile dirs (`core/conscious/__main__.py:298-299`,
  `consolidator.py:58-59`), routines (`__main__.py:151`), scratchpad
  (`__main__.py:276`), trigger YAML (`core/memory/triggers/`)
- Piper models `core/voice/models/` (`core/voice/tts.py:63`)
- Only `data/credentials.db` respects `ALFRED_DATA_DIR`
  (`core/identity/credentials.py:50-52`)

## Acceptance Criteria

- [ ] Single `ALFRED_DATA_DIR` (default `./data`) under which ALL mutable
      state lives in a documented layout (e.g. `memory/`, `models/`,
      `credentials.db`); paths derived in `AlfredConfig` properties — one
      construction site, no per-call-site path building.
- [ ] Semantic memory sources (preferences/profile/routines Markdown+YAML)
      get an explicit decision: they are user-editable content, not derived
      state — either move under the data dir or stay in-tree as seed content
      copied on first run. Decided and documented, honoring Pillar 4
      (core preference files never edited at runtime except by Librarian).
- [ ] Migration: on startup, if legacy in-tree paths exist and the data dir
      is empty, move (or copy + warn) — no silent data loss for the existing
      deployment.
- [ ] Container-friendly: the compose profile ticket mounts exactly one
      volume for durable state.
- [ ] Docs: `.env.example`, getting-started, and backup guidance name the
      one directory.

## Notes

- Research vault already configurable (`RESEARCH_VAULT_PATH`) — leave as-is,
  it's an output vault, not runtime state.
- `web/dist` is build output, not state — out of scope.
