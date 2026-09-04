# Blank env values crash config load — the `.env.example` promise is broader than the code

## Summary

`.env.example:3` tells every operator "Everything below OPTIONAL has a sane default;
leave blank to accept it." For the numeric vars that is false: a key present and empty
is `""`, not absent, so `int("")` / `float("")` raises at config load — in every service
at once, naming neither the variable nor the file. Either honour the promise everywhere
or narrow it, and give the numeric reads the same named-failure treatment
`positive_seconds` already gives `EMBEDDING_TIMEOUT_SECONDS`.

## Context / Motivation

The embedding and reflex vars were fixed on the `feat/vllm-embedding-adapter` branch
(`normalize_embedding_model`, `normalize_embedding_dim`, `positive_seconds`, and the
`OPENAI_COMPAT_HOST` → `LMSTUDIO_HOST` fallback), and
`tests/shared/test_env_example.py` now guards every key the file *ships* blank. What
that guard cannot reach is the keys that ship *with* a value, because blanking one is an
operator action, not a shipped state:

- **Ships with a value in `.env.example`, and the header invites blanking it:**
  `SESSION_TIMEOUT_MINUTES` (:64), `DAILY_COST_CAP_USD` (:67),
  `VOICE_CONFIDENCE_THRESHOLD` (:70), `KOKORO_SPEED` (:73), `REDIS_PORT` (:87),
  `MQTT_PORT` (:89). Verified 2026-09-04: `SESSION_TIMEOUT_MINUTES= alfred` →
  `ValueError: invalid literal for int() with base 10: ''` out of
  `AlfredConfig.from_env()`.
- **Undocumented but equally reachable via `alfredctl up -e KEY=`:**
  `CLAUDE_MAX_TOKENS` (`shared/config.py:323`), `INVOLUNTARY_RECALL_LIMIT` (:336),
  `INVOLUNTARY_RECALL_THRESHOLD` (:337), `CHANNELS_PORT`
  (`core/channels/__main__.py:35`), `TRIGGER_PORT` (`core/triggers/__main__.py:323`),
  `LIBRARIAN_INTERVAL_SECONDS` (`core/conscious/__main__.py:323`),
  `SPEAKER_ID_THRESHOLD` (`core/voice/speaker_id.py:65`).
- The two failure shapes are not equally bad: a bare `ValueError` at import kills the
  process before logging is up, while `positive_seconds`-style validation says which
  variable and what it got. The latter is what the whole surface should do.

Adjacent, same rule ("doctor must not describe a configuration the runtime would read
differently"), found in the same review:

- **`_check_reflex` ignores the `LMSTUDIO_HOST` fallback.** With `REFLEX_BACKEND=openai`,
  `OPENAI_COMPAT_MODEL` set, `OPENAI_COMPAT_HOST` absent and `LMSTUDIO_HOST` set,
  `AlfredConfig.from_env()` yields a working `openai_compat_host=http://lmstudio:1234`
  while `alfredctl doctor` prints `✗ fail — REFLEX_BACKEND=openai needs
  OPENAI_COMPAT_HOST and OPENAI_COMPAT_MODEL`. Verified 2026-09-04 by running both.
  `_check_embeddings` resolves its values through the `shared.config` helpers precisely
  to avoid this; `_check_reflex` (`alfredctl/doctor.py:94-119`) still re-derives.

## Acceptance Criteria

- [ ] Every numeric env read goes through a helper that treats blank as the default and
      names the variable, its unit and the offending value on failure (`positive_seconds`
      is the shape; ports and counts want an int equivalent).
- [ ] The strays in `core/channels`, `core/triggers`, `core/conscious` and
      `core/voice/speaker_id.py` use it too, or move into `AlfredConfig` — see
      `config-surface-unification.md`, which owns that migration.
- [ ] `tests/shared/test_env_example.py` is extended (or joined) by a check that blanking
      any documented key is equivalent to omitting it, not only the keys shipped blank.
- [ ] `.env.example:3` either becomes true or says which vars it does not cover.
- [ ] `_check_reflex` resolves its host through the same fallback `from_env` uses, so
      doctor and the runtime cannot disagree.

## Notes

- Overlaps `config-surface-unification.md` (stray `os.getenv` reads, `.env.example`
  drift). If both are picked up, do that one first — moving the reads into
  `AlfredConfig` shrinks this to "validate them there".
- Per `docs/backlog/README.md` non-sensitive work belongs in a GitHub Issue; this file
  was written by an agent with no GitHub auth, so mirror it when convenient.
