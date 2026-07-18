# Fix macOS ARM64 hard crash in full local pytest run (piper-tts espeakbridge)

**Priority:** medium
**Source:** Phase 0 green-gates verification on a fresh clone (2026-07-18)

## Summary
Running the full backend suite locally on macOS ARM64 hard-crashes the pytest process at
`tests/core/channels/test_web_server.py::test_ws_response_forwards_actions_taken_and_mood`.
Root cause is upstream: piper-tts's compiled `espeakbridge.so` ignores the configured
espeak-ng data path and falls back to a baked-in build-machine path. Reproduces on both
piper-tts 1.4.1 (locked) and 1.5.0 (latest) on a fresh clone; CI is unaffected (all jobs
run on ubuntu-latest).

## Context / Motivation
- Discovered during the 2026-07-18 fresh-clone gate verification; the rest of the suite is
  green (1106 passed, 2 skipped) when that test is deselected.
- Existing dev checkouts may not hit it (environment-dependent: espeak-ng data present at
  the baked path), which is why earlier local runs reported full green.
- Contributors on Apple Silicon following CONTRIBUTING.md will hit a process crash, which
  looks like a broken repo rather than an upstream bug.

## Acceptance Criteria
- [ ] Full `pytest` passes (or cleanly skips the affected test) on a fresh macOS ARM64
  clone with no crash.
- [ ] Fix is not a blanket test skip on macOS unless the upstream bug is documented and
  linked (prefer: guard TTS instantiation in tests, mock Piper at the boundary, or pin a
  fixed upstream release once available).
- [ ] CONTRIBUTING.md notes the workaround if one remains necessary.
