# Process Model — Why Multiple Processes Instead of One Async Process

Alfred runs as six supervised OS processes (`bridge`, `reflex`, `triggers`,
`conscious`, `channels`, `memory-ingestor` — see `runner/__main__.py`)
communicating through Redis Streams, rather than as one Python process with
asyncio tasks and thread pools. This document records why, because the
question resurfaces and the answer is grounded in Python-specific realities,
not general microservice fashion.

**The one-sentence version:** single-process async is the right call when
everything is I/O-bound and equally unimportant; Alfred has a hard-latency hot
path, crash-prone native ML, and user-facing servers in the same system, and
processes are the only isolation boundary Python actually enforces.

## 1. Latency isolation: the GIL makes threads a false promise

The System 1 budget — reflex event→action in <500ms — must hold while the same
system hosts 10–40s model loads and multi-second Whisper transcriptions.
In one process, "multi-threaded + async" does not protect that budget:
CPU-bound native inference (ctranslate2, ONNX, torch) saturates cores and
contends for the interpreter at every Python boundary crossing. `asyncio.to_thread`
moves blocking work off the *event loop* (see `core/channels/web_server.py`)
but a thread still competes for CPU with everything else in the process.
Across processes, the OS scheduler enforces fairness regardless of what Python
is doing: a transcription in `channels` cannot steal reflex's deadline. No
in-process mechanism in Python provides that guarantee. (Free-threaded
CPython does not change this yet — the ML stack is not production-ready on it.)

## 2. Fault isolation: native code crashes processes, not threads

The heaviest dependencies are C++/Rust under thin Python wrappers
(faster-whisper/ctranslate2, piper/onnxruntime, torch, sqlite-vec). When one
segfaults it takes the whole process; a thread boundary is no boundary. In
this design that means "web channel down ~2s while the supervisor restarts
it"; single-process it means Alfred is dead, including the light switches.
The supervisor (`runner/supervisor.py`) restarts crashed services with
exponential backoff, and per-service warmup failures are logged warnings, not
system outages.

## 3. Restart granularity is asymmetric — and that asymmetry is the point

Reflex restarts in ~1s; anything holding Whisper or the embedding model pays
10–40s. Per-process restarts mean each service pays only its own bill, which
is also what makes hot-reload development pleasant (edit conscious code, only
conscious restarts). Single-process, every crash or reload anywhere re-pays
the full model-loading cost everywhere.

## 4. Shared state that fails loudly instead of corrupting quietly

Processes share state only through Redis and SQLite (Pillar 3). Concurrency
bugs therefore surface as loud, diagnosable errors instead of silent
corruption. Case study (2026-07-16): `conscious` and `memory-ingestor`
migrating the shared `episodic_cold.db` schema concurrently raced — and the
race surfaced as `UNIQUE constraint failed: schema_version.version` with a
clear log line, fixed locally in `SqliteVecStore`. The single-process
equivalent is shared Python objects guarded by locks someone must remember to
take; races there don't throw constraint violations — they corrupt state
silently.

## 5. The boundaries are observable

Because the seams are Redis Streams, they can be inspected in production:
`XINFO GROUPS` shows which service consumes what, pending-entry lists (PEL)
show exactly which message is stuck (`scripts/smoke-test.sh` uses both for
diagnostics). Consumer groups give at-least-once delivery — an event in
flight when a service dies is redelivered on restart. In-process asyncio
queues and thread pools have no equivalent introspection, and they lose their
contents on crash.

## 6. The bus must exist anyway, so internal boundaries are nearly free

Sovereign external apps (home-service, signal-bridge, HA via MQTT) are
separate processes by design — Pillar 2. The Redis Streams protocol, Pydantic
schemas, and observability must exist for them regardless; drawing internal
boundaries along the same seams adds almost no machinery. It also keeps
deployment honest: processes map 1:1 to Compose containers in production, and
distributing services across machines later (GPU box for inference) is a
config change, not a rearchitecture.

## The honest costs

- ~1GB of duplicated embedding-model weights (`conscious` + `memory-ingestor`
  each load EmbeddingGemma; folding the ingestor into conscious remains a
  legitimate consolidation if memory pressure ever matters).
- A supervisor's worth of moving parts, and six log prefixes instead of one.
- Occasional cross-process coordination bugs (the schema race above) — real,
  but bounded and loud, per §4.
- Cross-process notification delivery needs a dispatch stream + per-process
  delivery workers, purely because WebSocket connections live in `channels`
  while the dispatcher logic lives in `conscious`.

These are bounded costs. The single-process alternative's costs are
unbounded: one process where a Whisper segfault kills the smart home, a
librarian consolidation steals reflex's deadline, and every bug hunt starts
with "which of the 40 tasks and 6 thread pools did it?"

## When this would be the wrong design

If Alfred were purely I/O-bound coordination — no local inference, no latency
floor, no user-facing servers sharing a box with background ML — a single
asyncio process would be simpler and better. The process model is justified
by this specific workload mix, not by principle.
