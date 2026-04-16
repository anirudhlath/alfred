# Startup Warmup Design

Eliminate first-request latency by eagerly initializing all lazy-loaded components during service startup.

## Problem

Multiple components use lazy initialization — models, database connections, search indexes, and integration adapters are only loaded on first use. This causes 10-60s latency on the first request depending on which components are hit.

| Component | Lazy Trigger | First-Use Latency |
|-----------|-------------|-------------------|
| EmbeddingGemma-300M | First `embed()` call | 10-20s |
| WhisperSTT (large-v3-turbo) | First audio transcription | 20-40s |
| PiperTTS (alan-medium) | First TTS synthesis | 10-15s |
| Ollama SLM | First reflex inference | 1-5s |
| SQLite cold store + migration | First memory operation | 0.5-2s |
| Integration adapters | First tool call per adapter | 0.5-2s |
| RediSearch index | First vector search | ~100ms |

## Approach

Each service warms its own components in its async startup, before entering the event loop. No new abstractions — just call lazy initializers eagerly. Independent warmup tasks run concurrently via `asyncio.gather` within each service.

## Per-Service Changes

### 1. Conscious Engine (`core/conscious/__main__.py`)

After creating components, before entering the consumer loop:

```python
async def _warmup_conscious(embedder, hot_store, cold_store):
    """Eagerly initialize all lazy-loaded components."""
    import time
    from loguru import logger

    async def _warm(name: str, coro):
        t0 = time.monotonic()
        await coro
        logger.info("warmup: {} ready ({:.1f}s)", name, time.monotonic() - t0)

    await asyncio.gather(
        _warm("embedding model", embedder.embed("warmup")),
        _warm("redis vector index", hot_store.ensure_index()),
        _warm("sqlite cold store", cold_store._connect()),
    )
```

Integration adapters: instantiate all registered adapters eagerly after the gather.

### 2. Channels (`core/channels/web_server.py` lifespan)

Eagerly create STT and TTS instances during FastAPI lifespan startup:

```python
# In lifespan, after existing init:
stt = _get_stt()      # triggers WhisperModel load
tts = await _get_tts() # triggers Piper download + ONNX load
```

Both run concurrently if possible (STT is sync/CPU-bound, TTS has async download).

### 3. Reflex Engine (`core/reflex/__main__.py`)

After creating the engine, before entering the consumer loop:

```python
# Warm Ollama — force model into GPU memory
from core.reflex.ollama_client import infer, _get_client
client = await _get_client()  # create httpx client
await infer("ping")           # minimal inference to load model
```

### 4. Memory Ingestor (`core/memory/ingestor_main.py`)

Same pattern as conscious — the ingestor creates its own embedder, hot store, and cold store instances:

```python
await asyncio.gather(
    _warm("embedding model", embedder.embed("warmup")),
    _warm("redis vector index", hot.ensure_index()),
    _warm("sqlite cold store", cold._connect()),
)
```

## Logging

Every warmup step logs completion with elapsed time:

```
INFO | conscious | warmup: embedding model ready (12.3s)
INFO | conscious | warmup: redis vector index ready (0.1s)
INFO | conscious | warmup: sqlite cold store ready (0.4s)
INFO | channels  | warmup: whisper STT ready (25.1s)
INFO | channels  | warmup: piper TTS ready (11.2s)
INFO | reflex    | warmup: ollama model ready (2.1s)
```

## What Does NOT Change

- Runner spawn order and inter-service delays (unchanged)
- Lazy-init code paths (kept as safety net — warmup calls them early, but they remain idempotent)
- Public APIs and interfaces
- No new files, classes, or abstractions

## Testing

- Existing tests continue to pass (lazy init still works)
- Manual verification: start Alfred, observe warmup logs, send first request — should respond in ~1-3s (Claude API latency only)
