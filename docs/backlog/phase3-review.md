# Phase 3 Code Review — Deferred Items

From code review of PR #9 (feature/phase3). All items resolved.

## Resolved

- **Missing `test_web_server.py`** — added with 6 tests (health, config, routes)
- **`SpeakerID` stub** — added `core/voice/speaker_id.py` with interface
- **Demo script Literal type** — fixed with `cast(ChannelType, channel)`
- **Regression runner pass logic** — extended `_check_scenario()` for positive-action scenarios
- **Voice test coverage** — added mocked transcription + synthesis tests (STT: 5 tests, TTS: 6 tests)
- **PWA icons** — added SVG icon + updated manifest.json
- **Static file path** — uses `Path(__file__).resolve()` instead of `os.path.join` traversal
- **MemoryRetrievalPrecision** — added stopword filtering + keyword extraction
- **Plan files in branch** — copied all Phase 3 step plans into feature branch
- **Web channel connection pooling** — uses shared Redis pool via FastAPI lifespan (fixed earlier)
- **Stream offset `0-0`** — `_publish_and_wait` captures current stream tail before publishing (fixed earlier)
- **PiperTTS temp file** — uses `delete=False` + manual cleanup in `finally` block (fixed earlier)
