# Startup Warmup Completes Before First Request and Reduces Latency

**Feature:** Model warmup at service startup
**Priority:** high
**Type:** functional

## Prerequisites
- Redis Stack running
- Ollama running with the configured model (default: gpt-oss)
- SQLite vector store accessible
- No cached models in memory (fresh startup)
- Logs viewable (stdout from runner or tail of log files)

## Test Steps
1. Stop all Alfred services (reflex, conscious, channels, triggers, memory-ingestor)
2. Start the runner and observe startup logs
3. Capture log lines containing "warmup[service]:" patterns (one per service)
4. Wait for all services to report ready status (should happen within 30-60s)
5. Send first request to each service (e.g., first voice request to channels, first conscious request, first reflex trigger evaluation)
6. Measure time from service start to "first request completes" for each
7. Compare latency: first request should complete within ~2-5s (not 30-40s)

## Expected Result
- Startup logs show warmup tasks for each service:
  - `warmup[reflex]: ollama model ready (XXXms)`
  - `warmup[conscious]: embedding model ready (XXXms)`, `redis vector index ready (XXXms)`, `sqlite cold store ready (XXXms)`
  - `warmup[memory-ingestor]: embedding model ready (XXXms)`, `redis vector index ready (XXXms)`, `sqlite cold store ready (XXXms)`
  - `warmup[channels]: whisper stt ready (XXXms)`, `piper tts ready (XXXms)`
- All services start accepting requests immediately (don't block on warmup)
- First request latency is normal (~2-5s), not blocked by model loading
- Logs show no exceptions during warmup or first request

## Notes
- Edge case: if a model fails to load during warmup (e.g., Ollama unavailable), warmup task logs the error but service continues; first request will trigger lazy-load (10-40s delay) — this is acceptable fallback behavior
- Warmup runs concurrently with service startup logic (not sequential) via `start_warmup()` task in background
- Each service's warmup components are independent (channels doesn't warm up embedding models, reflex doesn't warm up TTS, etc.)
- Lock protection (`_load_lock` in SentenceTransformerProvider, `_voice_load_lock` in channels) prevents race conditions if warmup and first request overlap
