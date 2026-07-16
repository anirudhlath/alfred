# Embedding Model Concurrent Load Safety (No Double-Load Race)

**Feature:** Memory system — embedding model thread safety
**Priority:** high
**Type:** regression

## Prerequisites
- Alfred conscious and memory-ingestor services running
- Redis Stack running
- Embeddings enabled in config
- Logs viewable
- Ability to trigger rapid memory operations (e.g., multiple requests to conscious engine in quick succession during startup)

## Test Steps
1. Start conscious and memory-ingestor services from a cold state (no cached models)
2. Immediately (within 1-2 seconds of startup, before warmup completes) send requests that trigger embedding:
   - Send a message to conscious (which calls episodic memory recall)
   - Send a trigger observation to the ingestor (via test script or mock event)
3. Observe logs for duplicate model-load messages or warnings
4. Check that embedding model loads exactly once, even with concurrent access
5. Verify both services complete their requests without errors or timeouts

## Expected Result
- Embedding model loads exactly once despite concurrent requests
- Logs show one `"Loaded embedding model: google/embeddinggemma-300m (dim=...)"` message
- No "model already loaded" or "lock timeout" errors
- Both requests proceed without duplicate model construction
- Memory operations (recall, ingestion) complete successfully

## Notes
- Thread safety is enforced by `_load_lock` (non-reentrant threading.Lock) in SentenceTransformerProvider._load()
- The lock protects against race condition: warmup task calls `embed("warmup")` → `_load()` while first request also calls `embed()` → `_load()`
- `asyncio.to_thread()` safely runs blocking `_load()` in worker thread; all callers wait on the lock, first one loads, rest get cached result
- Important: `self.dimension()` is NOT called during warmup (inside the lock) — instead we read dim directly from model to avoid re-entering _load() (deadlock)
- Edge case: if embedding model fails to load, second caller will raise; this is acceptable (both requests fail gracefully, not silently hang)
