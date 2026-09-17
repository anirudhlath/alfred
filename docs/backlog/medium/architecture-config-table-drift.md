# Delete the `docs/architecture.md` §6 Configuration Table (Wrong Defaults, Third Copy)

**Priority:** medium
**Source:** Task 8 of plan `2026-09-03-vllm-embedding-adapter.md` — noticed while documenting
the embedding backend seam; verified 2026-09-04

## Summary

`docs/architecture.md:773-785` carries an "Environment Variable | Default | Description"
table that is a hand-maintained third copy of `shared/config.py` and `.env.example`, and it
has rotted into being **actively wrong** rather than merely incomplete: it states an
`OLLAMA_MODEL` default the code does not use, so a contributor who follows it pulls the wrong
model and gets a Reflex engine requesting one that is not there. Eleven of the forty
environment variables `shared/config.py` reads appear in it; the System 2 API key, the
`REFLEX_BACKEND` seam and every `EMBEDDING_*` variable do not.

**This is not a regression from the vLLM embedding branch.** The wrong `OLLAMA_MODEL` default
entered the file in `7231fd6` (2026-03-10), and that branch's only change to §3.7/§3.7.2 left
§6 untouched. It is recorded here because the branch's own review surfaced it, not because it
broke anything.

## Context / Motivation

Verified against `shared/config.py` on 2026-09-04:

- **Wrong default — `OLLAMA_MODEL`.** The table (`docs/architecture.md:780`) says
  `gpt-oss:20b`; the code default is `llama3:8b` (`shared/config.py:326`). The same wrong
  default in the *README* is already filed as finding #20 of
  `docs/backlog/medium/fix-env-example-and-readme-drift.md` — that ticket covers the README
  and `.env.example`, and this one is the third copy it does not reach.
- **Wrong default — `RESEARCH_VAULT_PATH`.** The table (`:783`) says `./research`; the code
  default is `str(data_root() / "research")` (`shared/config.py:334`), i.e. an absolute path
  under `ALFRED_DATA_DIR` (default `data/`). Anyone provisioning `./research` provisions the
  wrong directory.
- **Omissions that matter.** No `OPENROUTER_API_KEY` / `CLAUDE_API_KEY` / `CLAUDE_MODEL`, so
  the System 2 engine this same document describes has no configuration entry at all. No
  `REFLEX_BACKEND` (nor `OPENAI_COMPAT_HOST`/`_MODEL`, `LMSTUDIO_HOST`), despite §3.2
  documenting that seam. No `EMBEDDING_BACKEND` / `EMBEDDING_HOST` / `EMBEDDING_MODEL` /
  `EMBEDDING_DIM` / `EMBEDDING_API_KEY` / `EMBEDDING_TIMEOUT_SECONDS`, despite §3.7.2 now
  documenting that seam. Also absent: `ALFRED_TRUSTED_NETWORKS`,
  `ALFRED_SECRETS_PASSPHRASE`, `ALFRED_DATA_DIR`/`ALFRED_DATA_MODE`, and the voice knobs.
- **Scale.** `grep -o 'os\.getenv("[A-Z_0-9]*"' shared/config.py | sort -u | wc -l` → 40.
  The table has 11 rows. Other modules read more env vars still.

The structural point is that this is the *third* place the same facts are written down —
`.env.example` (which the README already calls "the annotated source of truth"), the README's
"most common ones" table, and this one — and it is the copy with no reader-facing pressure to
stay right: a stranger reads the README, an operator reads `.env.example`, and this table is
read mostly by contributors and agents, who then propagate what it says. Each new
configuration knob currently owes an edit to all three or the newest surface is invisible in
the oldest doc, which is exactly how the two wrong defaults above survived six months.

## Acceptance Criteria

- [ ] The `docs/architecture.md` §6 table is **deleted**, replaced by prose naming
      `shared/config.py` as the code source of truth and `.env.example` as the annotated
      reference — matching the README, which already defers to `.env.example` — and keeping
      §6's genuinely architectural content (the `AlfredConfig` dataclass, the `redis_url`
      property, the `python-dotenv` auto-load, the `alfred-sdk` service variables), none of
      which is duplicated anywhere else.
- [ ] No copy of the deleted rows is reintroduced elsewhere in `docs/`; anything a reader
      would have wanted from them lives in `.env.example`, and any variable found missing
      there while doing this is added to `.env.example` rather than to a doc.
- [ ] `grep -rn 'OLLAMA_MODEL' docs/` surfaces no `gpt-oss:20b` claim, in §6 or anywhere else.

## Notes

- **The alternative that keeps costing** is to keep the table and merely correct it: fix the
  two defaults, add the ~29 missing variables, and accept that every future knob owes three
  edits instead of two. That is what has been done implicitly until now, and it is what
  produced a six-month-old wrong default — the table has no test, no CI check, and no reader
  who would notice.
- If the table is kept anyway, the cheapest guard is a test in the spirit of
  `tests/shared/test_env_example.py`: parse the markdown table and assert each row's default
  matches `AlfredConfig` — which is a real cost, and worth weighing against just deleting it.
- Related: `docs/backlog/medium/fix-env-example-and-readme-drift.md` (README + `.env.example`
  half of the same drift) and `docs/backlog/medium/config-surface-unification.md` (the shape
  of the config surface itself).
