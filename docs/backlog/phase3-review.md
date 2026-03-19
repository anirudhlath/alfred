# Phase 3 Code Review — Deferred Items

From code review of PR #9 (feature/phase3). Critical issues fixed inline.

## Should Fix

- **Missing `test_web_server.py`** — planned in Step 5 Task 4 but omitted. Add health endpoint test, WebSocket mock test, static file serving test.
- **`SpeakerID` stub** — planned as `core/voice/speaker_id.py` but omitted. Define the interface for voiceprint-based identity resolution.
- **Demo script Literal type** — `evals/e2e/demo_good_morning.py` uses `type: ignore[arg-type]` on channel param. Use a proper Literal type or cast.
- **Regression runner pass logic** — `evals/regression/runner.py` only marks "none" scenarios as passed. Extend to support positive-action scenarios.

## Nice to Have

- **Voice test coverage** — STT/TTS tests only check attribute existence. Add mocked logic tests.
- **PWA icons** — `web/manifest.json` has empty `icons` array, preventing mobile "Add to Home Screen".
- **Static file path** — `core/channels/web_server.py` uses `../../web` relative traversal. Consider `importlib.resources` or config-driven path.
- **MemoryRetrievalPrecision** — naive word-overlap heuristic. Extend with LLM-as-judge when DeepEval is fully wired.
- **Plan files in branch** — Phase 3 plan files exist in main repo `docs/superpowers/plans/` but not in the feature branch.
