# Project Alfred — Expanded Vision Design Specification

**Date:** 2026-03-19
**Status:** Draft
**Author:** Anirudh Lath + Claude (Lead Engineer / Background Scientist)
**Supersedes:** Phases 3-5 of the original spec (2026-03-10). Phases 1-2 remain as-is.

---

## 1. Vision Evolution

The original Alfred spec defined an ambient, event-driven Multi-Agent System for smart environments. This expansion transforms Alfred into a **full personal AI assistant** — voice-first, conversational, proactive, and capable of managing all aspects of daily life — while retaining the ambient intelligence that makes Alfred unique.

Alfred is no longer just a smart home brain. He is Alfred Pennyworth: a butler who manages the estate (ambient), answers when spoken to (conversational), anticipates needs (proactive), and handles anything you ask (general-purpose).

### What Changes

| Aspect | Original Spec | Expanded Vision |
|--------|--------------|-----------------|
| Scope | Smart home automation | Full personal assistant + smart home |
| Interaction | Headless (event-driven only) | Voice + chat + ambient |
| Intelligence | System 1 (local SLM) only until Phase 3 | System 1 + System 2 (Claude) from Phase 3 |
| Domains | Home + media | Home + calendar + health + finance + weather + tasks + more |
| Memory | Preferences + scratchpad | Three-layer biologically-inspired (episodic + semantic + procedural) |
| Channels | None (event bus only) | Web PWA, Signal, future extensible |
| Personality | None | Classic butler — formal, dry wit, opinionated |

### What Stays the Same

The Four Pillars remain non-negotiable. The event bus, Reflex Engine, Trigger Engine, domain agents, SDK, tool registry, and all Phase 1-2 work are unchanged. This spec builds on top of the proven foundation.

---

## 2. System Architecture

Alfred operates across three planes:

```
┌─────────────────────────────────────────────────────┐
│                   Interaction Layer                   │
│   Web PWA (voice+chat)  │  Signal Bridge  │  Future  │
└───────────┬─────────────┴───────┬─────────┴──────────┘
            │ user requests       │
            ▼                     ▼
┌─────────────────────────────────────────────────────┐
│              Conscious Engine (System 2)              │
│  Claude · Conversation · Reasoning · Briefings       │
│  IntegrationRegistry · Identity Gate                 │
└───────────┬──────────────────────┬───────────────────┘
            │ actions              │ reads/writes
            ▼                     ▼
┌──────────────────────┐  ┌────────────────────────────┐
│   Event Bus (Redis)  │  │      Memory Plane           │
│   alfred:events      │  │  Episodic · Semantic · Proc │
│   alfred:actions     │  │  Consolidation (Librarian)  │
└───────────┬──────────┘  └────────────────────────────┘
            │ events
            ▼
┌─────────────────────────────────────────────────────┐
│           Ambient Plane (System 1)                    │
│  Reflex Engine · Trigger Engine · Domain Agents      │
│  home-service · future services                      │
└─────────────────────────────────────────────────────┘
```

### Ambient Plane (System 1) — Existing

The Reflex Engine, Trigger Engine, and domain agents operate as built. Fast, local, event-driven, no user interaction needed. System 1 continues to handle ambient events (motion → lights, temperature → HVAC, scheduled triggers) at sub-500ms latency using the local SLM via Ollama.

### Conversational Plane (System 2) — New

The Conscious Engine handles all direct user interactions: voice, Signal, web chat. Powered by Claude. Has access to the same tool registry and domain agents as System 1, plus integrations (calendar, health, finance, weather). See Section 3.

### Memory Plane — Extended

Three-layer biologically-inspired memory serving both planes. System 1 reads semantic memory (preferences) as it does today. System 2 reads and writes all three layers. See Section 4.

### Plane Independence

System 1 and System 2 share the event bus and tool registry but operate independently. System 1 never waits for System 2. System 2 can observe what System 1 did and override or follow up.

**Conflict resolution:** System 1 and System 2 operate on different event types by default. A **static event routing table** (configuration, not inference) determines which events System 2 is interested in. For example, `UserRequest` events always go to System 2, `StateChangedEvent` always goes to System 1. For events where both systems might act (e.g., a door opening that System 1 reacts to and System 2 might want to mention), System 1 acts immediately and System 2 observes the action result asynchronously — no grace period, no race condition. System 2 can then follow up conversationally if relevant ("Sir, the front door opened at 3 AM — System 1 turned on the hallway light, but I thought you should know.").

---

## 3. Conscious Engine

The conversational brain of Alfred. Lives at `core/conscious/`.

### Responsibilities

- Receive user requests from any channel (voice, Signal, web)
- Maintain conversation context (working memory)
- Reason about requests using Claude
- Call tools (via ToolRegistry) and integrations (via IntegrationRegistry)
- Generate responses routed back to the originating channel
- Read/write all three memory layers
- Enforce identity gate before processing

### Request Flow

```
User input (voice/Signal/web)
    → UserRequest schema on Redis stream (alfred:user:requests)
    → Identity Gate (is this sir or a guest?)
    → Context Assembly:
        │ Pull relevant semantic memory (preferences, facts)
        │ Pull relevant episodic memory (recent conversations, events)
        │ Pull procedural memory (known routines)
        │ Pull live context (ContextReader — HA state, integrations)
    → Claude inference (with assembled context as system prompt)
    → Response:
        │ Text/voice response → back to originating channel
        │ ActionRequests → event bus → domain agents (same as System 1)
        │ Memory writes → scratchpad (episodic observations)
        │ Trigger creation → Trigger Engine (if Alfred decides to schedule)
```

### Interaction with System 1

The Conscious Engine does NOT replace the Reflex Engine. They coexist:

- Motion sensor at 2 AM → System 1 handles it instantly (turn on hallway light)
- You say "Good morning" → System 2 handles it (briefing, personality, multi-step)
- System 1 processes a door-open event → System 2 can observe this and mention it conversationally

### Agentic Loop (Multi-Step Tool Use)

The Conscious Engine supports iterative tool use within a single request. Claude can call tools, receive results, reason, and call more tools — an agentic loop:

```
User request → Claude inference
    → Tool call (e.g., get calendar) → result
    → Claude reasons on result → another tool call (e.g., get weather) → result
    → Claude assembles final response
```

**Constraints:**
- Maximum iterations per request: configurable (default 10)
- Each iteration counts against the token budget for that request category
- If budget exhausted mid-loop, Claude must return a partial response explaining what it couldn't complete
- Tool calls within a loop are parallelized where possible (Claude can request multiple tools in one turn)
- All tool calls within a loop share the same trace (nested spans)

For the "Good morning" briefing, Claude calls all integrations in parallel in a single tool-use turn, then assembles the response — typically 2 iterations (tool calls + final response).

### Conversation State

Each channel maintains its own conversation thread. Cross-channel continuity is supported via `session_id` — start on voice, switch to Signal, Alfred can continue the same thread. Sessions expire after configurable idle time (default 30 min).

---

## 4. Memory Architecture

Biologically-inspired, three-layer memory system modeled on human cognition.

### Design Principles (from Neuroscience)

- **Not everything gets stored** — attention and relevance filter what persists
- **Consolidation during "sleep"** — the Librarian replays, strengthens, or discards
- **Retrieval is reconstructive** — context-driven recall, not full replay
- **Forgetting is a feature** — irrelevant memories decay, keeping the system useful
- **Unlearning is active** — contradictory signals update or demote existing knowledge

### Layer 1: Episodic Memory — "What Happened"

Timestamped records of events, conversations, and observations.

```
Storage: Redis Stream (hot, last 7 days) + SQLite archive (cold, searchable)
Schema: EpisodicEntry(timestamp, source, summary, entities, valence: Literal["positive", "negative", "neutral"])
```

- Every conversation turn, notable event, and System 1 action gets logged
- Entries are **summarized, not raw** — "Sir asked for a briefing and seemed rushed" not the full transcript
- **Retrieval:** Semantic search (embedding similarity) + time-based + entity-based ("what happened with the front door last week?")

**Embedding & vector storage:**
- Embedding model: local sentence-transformer (e.g., `all-MiniLM-L6-v2` or similar, runs on CPU/GPU). Exact model selected during implementation based on accuracy/speed benchmarks.
- Vectors stored alongside entries: in Redis as binary field (hot), in SQLite via `sqlite-vec` extension (cold)
- Embedding computed at write time (when episodic entry is created), not at query time
- Query: embed the search query with the same model, cosine similarity against stored vectors, combine with recency weighting

**SQLite schema (cold storage):**
- Table: `episodic_entries(id TEXT PK, timestamp REAL, source TEXT, summary TEXT, entities JSON, valence TEXT, embedding BLOB)`
- `sqlite-vec` virtual table for vector similarity search
- Migration strategy: single `schema.sql` in `core/memory/`, applied at startup if not present. Schema versioning via a `schema_version` table.

**Decay schedule:**
- 0-7 days: Full entries in Redis (hot)
- 7-90 days: Compressed summaries in SQLite (multiple entries → single summary)
- 90 days - 1 year: Only Librarian-flagged significant entries survive
- 1 year+: Archived, retrievable only on explicit request

### Layer 2: Semantic Memory — "What I Know"

Learned facts, preferences, and inferences about the user and their world.

```
Storage: Markdown files with YAML frontmatter (existing pattern)
Location: core/memory/preferences/ (existing) + core/memory/profile/
```

- `preferences/lighting.md`, `preferences/sleep.md` — behavioral preferences (existing)
- `profile/about.md` — learned facts ("commute is 25 min to downtown", "allergic to shellfish")
- `profile/relationships.md` — people Alfred knows about ("Sarah — friend, visited 3 times, prefers 68F")
- Every inference is tagged: `[inferred from 3 observations, confidence: high, source: episodic, date: 2026-03-19]`
- User can inspect, edit, or delete any entry — Markdown is human-readable by design
- Updated only by the Librarian during consolidation, never at runtime — with one exception: **onboarding** seeds initial preferences via a dedicated bootstrap path that writes directly to semantic memory before the Librarian's first run

**Conflict resolution for semantic updates:** When new observations contradict existing facts (e.g., user changed jobs, new commute), the Librarian:

1. Detects conflicting episodic entries vs existing semantic fact
2. If new pattern is consistent (configurable: default N=5 observations over M=14 days), updates the semantic entry
3. Logs the change with provenance: `[updated 2026-04-15: was "25 min downtown", now "40 min to campus", source: 12 observations over 14 days]`
4. Old value archived — Alfred can recall "you used to work downtown"

### Layer 3: Procedural Memory — "How to Do Things"

Learned routines, patterns, and multi-step behaviors.

```
Storage: YAML files in core/memory/routines/
Schema: RoutineSpec(name, trigger_pattern, steps, confidence, learned_from)
```

- Not hardcoded automations — patterns Alfred has **observed and proposed**
- Example: Alfred notices you dim lights and start Netflix every evening around 8pm. He creates a candidate routine, asks once: "Sir, I've noticed you tend to start a film around 8. Shall I prepare the usual?" If confirmed, it becomes a procedural memory.
- Routines can be promoted to triggers (via the Trigger Engine) once confirmed
- Unconfirmed patterns decay if the behavior stops

**Promotion pipeline:**

```
Observation → Pattern detected (silent)
    → Candidate routine (stored, not acted on)
    → Confidence threshold reached → Suggest once to user
        → Accepted → Active routine (can become a trigger)
        → Rejected → Marked rejected, won't suggest again
        → Ignored → Decays naturally
```

**Unlearning / Habit change detection:**

```
Active routine
    → Monitoring: Alfred tracks hit rate (did the pattern occur?)
    → 3 consecutive misses → Confidence drops
    → Confidence below threshold → Routine demoted to "dormant"
    → Dormant + 30 more days of no activity → Routine archived
    → Archived routines can be recalled ("you used to...") but never acted on
```

Three states for any learned knowledge:

| State | Meaning | Behavior |
|-------|---------|----------|
| **Active** | Recent, high confidence | Acts on it / uses in reasoning |
| **Dormant** | No recent signal, not contradicted | Ignores unless explicitly asked |
| **Archived** | Contradicted or very old | Only surfaces as "you used to..." |

### Consolidation — The Librarian (Extended)

```
Scratchpad (runtime writes)
    → Nightly Librarian run (Claude-powered)
        → Extract episodic entries → Episodic Memory
        → Extract/update preferences and facts → Semantic Memory
        → Detect repeated patterns → Candidate routines → Procedural Memory
        → Apply decay to old episodic entries
        → Resolve contradictions (last-observation-wins for facts, LLM-arbitrated for preferences)
        → Archive processed scratchpad
```

On failure, the scratchpad accumulates and the Librarian retries next cycle — memory files are never left in a partial state.

### Data Philosophy

Raw data stays in source systems (Apple Health, Apple Calendar, Robinhood). Alfred's memory stores only **inferences and preferences**, not raw data. The user can inspect, edit, or delete any inference Alfred has made.

---

## 5. Identity & Security

Single-user system with guest awareness.

### Authentication Model

```
User interaction arrives
    → Channel-based identity:
        │ Signal → phone number (only registered number is "sir")
        │ Web PWA → session token (WebAuthn / Face ID / Touch ID)
        │ Voice → speaker fingerprint (SpeechBrain ECAPA-TDNN, on-device)
    → Identity resolved:
        │ "sir" → full access
        │ "unknown" → guest mode
```

### Layered Authentication by Risk

Voice ID alone is not secure enough for sensitive actions (~2-3% equal error rate, vulnerable to replay and voice cloning). Authentication is layered by action risk:

| Risk Level | Examples | Required Auth |
|------------|----------|---------------|
| Low | Lights, music, weather, time | Voice ID sufficient |
| Medium | Calendar, briefing, routines | Voice ID + device proximity (same WiFi) |
| High | Unlock door, view finance data | Voice ID + explicit confirmation |
| Critical | Send money, delete data | Device biometric required (Face ID / Touch ID via PWA or Signal) |

### Extensible Identity Providers

The Identity Gate accepts any combination of factors that meets the risk threshold. New auth methods plug in without changing the gate logic:

- SpeechBrain voice fingerprint (enrolled at onboarding)
- WebAuthn / FIDO2 (Face ID, Touch ID, fingerprint via browser)
- Signal phone number verification
- Device proximity (WiFi / Bluetooth)
- Apple Watch wrist detection
- Future: NFC ring, hardware key

### Guest Mode

Alfred maintains his personality with guests — same formal, dry-witted butler. He simply treats them as a guest of the household: polite, helpful with allowed actions, firmly discreet about anything personal.

| Capability | Sir | Guest |
|------------|-----|-------|
| Conversation | Full (personal, opinionated) | Same personality, impersonal content |
| Smart home controls | Full | Limited (lights, music — configurable allowlist) |
| Personal data | Full | Blocked entirely |
| Memory | Reads and writes all layers | No reads, no writes |
| Triggers/routines | Create, modify, delete | None |

Example guest interaction: "Good evening. I'm Alfred. I'm afraid I'm not at liberty to discuss sir's schedule, but I'd be happy to adjust the lighting for you."

### Security Boundaries

- **Identity gate is at the Conscious Engine entry point** — before context assembly, before Claude sees anything. If identity = guest, the system prompt contains zero personal data.
- **System 1 (ambient) is identity-agnostic** — motion → lights works for anyone. It never generates user-facing responses.
- **Signal bridge** only accepts messages from the registered phone number. Others are dropped with no response but **logged for security auditing** (timestamp, sender number, message hash — no content).
- **Web PWA** requires authentication. No anonymous access.
- **Integration API keys** stored in local secrets manager, never in memory files or git.
- **Integration data sanitization** — all adapter responses pass through a scanner before injection into Claude's context, stripping anything that resembles prompt injection instructions.
- **Action confirmation for destructive operations** — Alfred never executes high-risk actions without explicit confirmation, regardless of prompt content.

---

## 6. Interaction Channels

### Channel Architecture

Each channel is an independent service that translates its native protocol into a `UserRequest` on Redis and renders `AlfredResponse` back to the user.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Web PWA    │     │Signal Bridge│     │  Future     │
│  (voice+    │     │  (signal-   │     │  (iMessage, │
│   chat UI)  │     │   cli)      │     │   Telegram) │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       ▼                   ▼                   ▼
   ┌──────────────────────────────────────────────┐
   │         alfred:user:requests (Redis)          │
   └──────────────────────┬───────────────────────┘
                          │
                          ▼
                  Conscious Engine
                          │
                          ▼
   ┌──────────────────────────────────────────────┐
   │        alfred:user:responses (Redis)          │
   └──────────────────────────────────────────────┘
       │                   │                   │
       ▼                   ▼                   ▼
   Web PWA            Signal Bridge         Future
```

### Channel Contracts (Pydantic Schemas)

- `UserRequest` — unified inbound. Includes: `channel`, `session_id`, `identity_claim`, `content_type` (text/audio), `content`, `timestamp`. Voice channels include transcribed text + audio reference.
- `AlfredResponse` — unified outbound. Includes: `channel`, `session_id`, `text`, `voice_audio` (optional TTS bytes), `actions_taken`, `mood` (personality cue for UI rendering).

### Dual Transport for Latency

Real-time channels use WebSocket for streaming. Async channels use Redis streams.

```
Voice (low latency):  PWA ←WebSocket→ Conscious Engine (streaming)
Signal (async):       Signal Bridge → Redis → Conscious Engine → Redis → Signal Bridge
```

Both paths feed the same engine, same memory, same identity gate. The WebSocket path writes to Redis **asynchronously** (fire-and-forget after processing) so every interaction is logged for observability and audit, but the user-facing response is not gated on the Redis write completing.

### Voice Latency Budget (with Streaming)

```
You finish speaking
    → 200-300ms  Whisper STT (whisper.cpp, large-v3-turbo, local GPU)
    → ~100ms     Identity gate + context assembly
    → 800-1200ms First Claude tokens arrive
    → TTS starts streaming immediately (Piper, local)

Time to first word from Alfred: ~1.2-1.6 seconds
```

Simple commands ("turn off the lights") route to System 1 via fast-path: under 1 second total.

### Web PWA

- Push-to-talk button → browser MediaRecorder API → audio to voice endpoint
- STT (Whisper) runs server-side on local hardware
- TTS response streamed back as audio
- Text chat panel alongside voice
- Responsive — works on phone and desktop
- WebAuthn for authentication (Face ID / Touch ID on Apple devices)
- Tech: lightweight framework (vanilla JS or Svelte), served from Alfred's server

### Signal Bridge

- Sovereign service in its own repo (`signal-bridge/`)
- Uses `signal-cli` under the hood (**requires JRE 17+** — non-Python dependency, must be in Containerfile)
- Registers as a linked Signal device
- Uses `alfred-sdk` to publish events: `AlfredClient.publish_request(user_request)` writes to `alfred:user:requests`. This avoids the bridge having a direct Redis dependency and maintains the SDK boundary (Pillar 2).
- Reads `AlfredResponse` from Redis via SDK subscription
- Only accepts messages from registered phone number
- Supports text and voice messages (voice messages transcribed via STT first)

### Cross-Channel Continuity

A `session_id` links conversations across channels. Start on voice, switch to Signal — Alfred continues the thread. Sessions expire after configurable idle time (default 30 min).

---

## 7. Integration Registry & Adapters

How Alfred connects to external data sources.

### Pattern

Mirrors the existing `ToolRegistry` and `TriggerRegistry`. Each integration is a Python module implementing an ABC base class.

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel

class IntegrationRequest(BaseModel):
    action: str
    params: dict[str, Any]  # Subclasses define typed params per action

class IntegrationResult(BaseModel):
    data: dict[str, Any]
    freshness: datetime
    confidence: float  # 0.0-1.0, how reliable is this data

class IntegrationCapability(BaseModel):
    name: str
    description: str
    params_schema: dict[str, Any]  # JSON Schema for action params

class Integration(ABC):
    name: str
    category: str  # "calendar", "health", "finance", "weather"

    @abstractmethod
    async def get_capabilities(self) -> list[IntegrationCapability]: ...

    @abstractmethod
    async def execute(self, request: IntegrationRequest) -> IntegrationResult: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
```

All request/response types are Pydantic models (Pillar 3). Individual adapters may define typed subclasses of `IntegrationRequest` for compile-time safety.

### IntegrationRegistry

Discovers and manages adapters at startup. The Conscious Engine queries it dynamically — no hardcoded integration lists. New adapters register via `@IntegrationRegistry.register()` decorator, same pattern as triggers.

### Relationship to ToolRegistry and DomainRouter

Three registries serve distinct purposes:

| Registry | What it holds | Who uses it | Direction |
|----------|--------------|-------------|-----------|
| **ToolRegistry** | MCP tool manifests from sovereign microservices (home-service, etc.) | Reflex Engine (System 1) + Conscious Engine (System 2) | Alfred → external services |
| **IntegrationRegistry** | Data-fetching adapters (calendar, health, weather, finance) | Conscious Engine (System 2) only | Alfred → external APIs |
| **DomainRouter** | Maps `target_service` → domain agent for action dispatch | Both systems, replaces hardcoded `HomeAgent` | Alfred → domain agents → services |

The distinction: **ToolRegistry** is for action execution (turn on light, play music). **IntegrationRegistry** is for data retrieval (get calendar, get sleep data). **DomainRouter** is for dispatching action results to the correct domain. They do not overlap — an engineer always knows which registry a capability belongs to.

### Adapter Contract

- Each adapter handles its own auth (API keys from secrets manager)
- Each adapter has a `health_check()` — Conscious Engine knows what's available
- Adapters are async with timeouts — a slow API cannot block the engine
- Results are `IntegrationResult(data, freshness, confidence)` — the engine knows data staleness
- All adapter responses pass through a sanitization layer before reaching Claude's context

### First Adapters

| Adapter | Source | Data | Notes |
|---------|--------|------|-------|
| `apple_calendar.py` | Apple Calendar (CalDAV) | Events, schedules | CalDAV protocol, works with any Apple Calendar account |
| `apple_health.py` | Apple HealthKit | Sleep, activity, heart rate | Needs iOS bridge: Health Auto Export app or Shortcuts automation pushing to local endpoint |
| `weather.py` | Open-Meteo (free API) | Forecast, current conditions | Uses client location, no API key needed |
| `robinhood.py` | Robinhood (unofficial API) | Portfolio, positions, P&L | robin_stocks library |

### Location: `core/integrations/`

```
core/integrations/
├── __init__.py
├── registry.py              # IntegrationRegistry
├── base.py                  # Integration ABC, IntegrationCapability, IntegrationResult
├── sanitizer.py             # Response sanitization (prompt injection defense)
├── apple_calendar.py
├── apple_health.py
├── weather.py
└── robinhood.py
```

---

## 8. Proactive Notifications

Alfred is opinionated and initiates contact when warranted.

### Notification Triggers

- Trigger Engine fires a proactive trigger
- Alfred observes an anomaly ("front door opened at 3 AM, no known device home")
- Integration data warrants attention ("your portfolio dropped 5% in the last hour")
- Routine reminder ("you have a meeting in 10 minutes")
- System alert ("home-service is down, ambient actions are degraded")

### Channel Selection

| Priority | Channel | Behavior |
|----------|---------|----------|
| **Informational** | Signal (silent) | "Your package was delivered." User sees it when they check. |
| **Important** | Signal + PWA push notification | "You have a meeting in 10 minutes." |
| **Urgent** | Voice announcement (if home) + Signal | "Sir, water leak detected in the basement. I've shut off the main valve." |

### Do Not Disturb

- Alfred reads calendar for meetings and sleep schedule from health data
- During DND windows: non-urgent notifications are queued, delivered after
- Urgent notifications (safety, security) always break through
- User can manually set DND via voice or Signal ("Alfred, hold my calls for an hour")

### Proactivity Levels

Three configurable levels (selected at onboarding, changeable anytime):

| Level | Behavior |
|-------|----------|
| **Opinionated** (default) | Suggests routines at lower confidence, comments on habits, offers unsolicited advice ("Sir, that's the third espresso today.") |
| **Moderate** | Suggests only high-confidence routines, limits unsolicited commentary to important observations |
| **Conservative** | Only speaks when spoken to, plus safety/security alerts |

---

## 9. System Prompt Assembly

The Conscious Engine builds Claude's prompt dynamically for every request. No hardcoded prompts.

### Prompt Composition

```
System prompt =
    Base personality (core/conscious/prompts/personality.md)
    + Identity context (sir: full profile | guest: minimal)
    + Available tools (from ToolRegistry — dynamic)
    + Available integrations (from IntegrationRegistry — dynamic)
    + Relevant semantic memory (preferences, facts — retrieved by relevance)
    + Relevant episodic memory (recent events, conversations — retrieved by relevance)
    + Active procedural memory (routines Alfred knows about)
    + Live context (HA state via ContextReader, integration data if needed)
    + Proactivity level instruction
    + Cost/token budget for this request category
```

### Personality

Stored in `core/conscious/prompts/personality.md` — versioned, editable, not buried in code.

Character: Alfred Pennyworth. Formal British butler. Dry wit. Understated. Opinionated when it matters. Discreet about personal information. Treats guests with the same courtesy but none of the familiarity.

### Context Window Management

Claude's context is finite. The prompt assembler prioritizes:

1. Personality + identity (always included, small)
2. Tools + integrations (always included, manifest only)
3. Conversation history for this session (most recent, trimmed to fit)
4. Relevant semantic memory (top-K by relevance to current request)
5. Relevant episodic memory (top-K by relevance + recency)
6. Live context (only if request requires it)
7. Procedural memory (only active routines)

Lower-priority items are trimmed first if the context window is tight.

---

## 10. Generic Domain Routing

The current runner hardcodes `HomeAgent`. Both System 1 and System 2 need a `DomainRouter` that dispatches `ActionRequest` to the correct domain agent by `target_service`.

### DomainRouter

```python
class DomainRouter:
    """Routes ActionRequests to the appropriate domain agent."""

    def register(self, service_pattern: str, agent: DomainAgent) -> None: ...
    async def route(self, action: ActionRequest) -> ActionResult: ...
```

- Agents register at startup: `router.register("home-service", home_agent)`
- The router reads `action.target_service` and dispatches
- Unknown services return an error result
- Adding a new domain = adding a new agent + registering it. No router changes needed.

---

## 11. Observability

Full distributed tracing with OpenTelemetry, visualized in SigNoz.

### Trace Structure (Example: "Good Morning" Briefing)

```
Trace: "good_morning_briefing" (root)
├── Span: stt_transcription (duration, model, confidence)
├── Span: identity_gate (result: sir/guest, method, confidence)
├── Span: context_assembly
│   ├── Span: integration.apple_calendar (status, latency, items_returned)
│   ├── Span: integration.apple_health (status, latency)
│   ├── Span: integration.weather (status, latency)
│   ├── Span: integration.robinhood (status, latency)
│   ├── Span: context_reader.home_assistant (status, entities_count)
│   ├── Span: memory.episodic_recall (query, results_count)
│   └── Span: memory.semantic_read (files_read)
├── Span: conscious_engine.inference
│   ├── Attribute: model
│   ├── Attribute: prompt_tokens
│   ├── Attribute: completion_tokens
│   ├── Attribute: time_to_first_token
│   └── Attribute: total_latency
├── Span: tts_synthesis (duration, model, audio_length_ms)
└── Span: channel_response (channel, delivery_latency)
```

### Implementation

- `shared/tracing.py` extended with OpenTelemetry SDK spans (already has `TraceRecord`)
- `@traced` decorator on integration methods, engine methods, STT/TTS
- OTLP exporter → SigNoz (self-hosted on CachyOS server)
- Trace IDs propagated through Redis streams and WebSocket sessions

### Metrics Collected

**System Health:**
- Redis stream lag, integration health check status, WebSocket connections
- Memory store sizes (episodic count, semantic file sizes, procedural routines count)

**Inference Quality:**
- Tool call success/error rate, System 1 → System 2 escalation rate
- Confidence scores, hallucination signals, conversation turn count per session

**User Experience:**
- Time-to-first-word (voice), end-to-end latency per channel
- Interaction frequency by time of day, proactivity acceptance rate

**Memory & Learning:**
- Librarian consolidation duration and outcomes
- Episodic retrieval relevance, semantic staleness, procedural lifecycle events
- Decay events — what got forgotten and why

**Cost:**
- Claude API spend (daily/weekly/monthly)
- Token usage by category (briefings, conversations, Librarian)
- Local vs cloud inference ratio

**Research-Specific:**
- Context window utilization, memory compression ratio, preference drift rate

### Dashboards (SigNoz)

- Latency waterfall per request type
- Integration health overview
- Claude token usage and cost trends
- Memory lifecycle visualization
- System 1 vs System 2 event distribution

---

## 12. Evaluation Strategy

Three-layer eval approach with no tool overlap.

### Layer 1: System 1 Evals (Existing Runner)

The existing `evals/` runner tests the Reflex Engine: event → SLM → expected action. Extended with:

- **Regression mode** (mocked Ollama) — canned SLM responses for fast, deterministic CI runs
- Scenario format unchanged — YAML files in `evals/scenarios/`

### Layer 2: System 2 Evals (DeepEval)

DeepEval (Apache 2.0, fully free) evaluates the Conscious Engine via pytest integration.

**Standard metrics:**
- Correctness — did Alfred get facts right from integrations?
- Tool use — right integrations called with right parameters?
- Coherence — does the response make sense across turns?
- Hallucination — did Alfred invent data?

**Custom metrics:**
- `ButlerPersonalityScore` — LLM-as-judge: does this response sound like Alfred Pennyworth?
- `PrivacyLeakScore` — scan response for personal data when identity = guest
- `ProactivityRelevanceScore` — was the unsolicited suggestion actually useful?
- `MemoryRetrievalPrecision` — of memories pulled into context, how many were used?

### Layer 3: End-to-End Traces (SigNoz)

Full pipeline simulation with mocked integrations. Assert on trace completeness, latency budgets, span presence. Replay scenarios and inspect the waterfall.

---

## 13. Logging

Structured logging using Loguru, replacing the current stdlib `logging.basicConfig()` pattern.

### Why Loguru

- Zero-config setup vs verbose stdlib boilerplate
- Built-in `bind()` for context propagation (`request_id`, `session_id`, `identity`)
- Native JSON serialization for structured log shipping
- Beautiful colored output in dev, machine-readable in prod
- Full variable values in exception tracebacks

### Setup: `shared/logging.py`

Single setup function replaces all `logging.basicConfig()` calls across entry points (resolves existing backlog item).

### Log Context Fields

Every log line carries: `timestamp`, `level`, `service`, `request_id`, `session_id`, `identity`, `trace_id`.

### Sinks

| Sink | Purpose | Environment |
|------|---------|-------------|
| Console (colored) | Developer readability | Dev |
| JSON file (rotated) | Persistent logs, searchable | Dev + Prod |
| OTLP (SigNoz) | Correlated with traces and metrics | Prod |

### Rotation & Retention

- File rotation: 500 MB per file
- Retention: 30 days
- Configurable via `AlfredConfig`

---

## 14. Configuration Management

### Existing: `shared/config.py`

`AlfredConfig` loads `.env` via python-dotenv. Extended with new fields:

**New config fields:**
- `CLAUDE_API_KEY` — Anthropic API key
- `CLAUDE_MODEL` — model ID (default: `claude-opus-4-6`)
- `PROACTIVITY_LEVEL` — opinionated / moderate / conservative
- `DAILY_COST_CAP` — Claude API spend cap in dollars
- `VOICE_CONFIDENCE_THRESHOLD` — speaker ID minimum confidence (default: 0.85)
- `SESSION_TIMEOUT_MINUTES` — conversation session expiry (default: 30)
- `EPISODIC_HOT_DAYS` — days before episodic entries move to cold storage (default: 7)
- `EPISODIC_COMPRESS_DAYS` — days before compression (default: 90)
- `SIGNAL_PHONE_NUMBER` — registered phone number for Signal bridge

### Secrets

API keys and credentials stored in `.env` only. Never in memory files, config files, or git.

### Runtime-Tunable Settings

Some settings should be changeable without restart (via Redis key or config file watch):
- Proactivity level
- Daily cost cap
- DND schedule
- Guest mode allowlist

The PWA settings page can update these directly.

---

## 15. Graceful Degradation

Alfred must remain useful when components fail. A butler who goes silent when one thing breaks is not trustworthy.

| Failure | Impact | Alfred's Response |
|---------|--------|-------------------|
| Claude API down | System 2 offline | System 1 continues ambient actions. Signal message: "Sir, I'm operating in reduced capacity. Complex requests will need to wait." |
| Integration timeout | Missing data in briefing | Skip it, mention it: "I wasn't able to reach your calendar this morning." |
| No internet | Cloud LLM + external APIs unavailable | System 1 fully operational (Ollama is local). Voice pipeline works (Whisper + Piper are local). Alfred is degraded but not dead. |
| Redis down | Event bus offline | Critical dependency. Supervisor auto-restarts. Each service has a brief in-memory queue (configurable, default 100 events) to absorb a Redis restart without losing events. If Redis is down beyond queue capacity, services log and drop. |
| Signal bridge down | Chat unavailable | Voice and PWA still work. Alfred mentions it if asked. |
| SigNoz down | No observability | Zero impact on functionality. Logs continue to file. |
| Librarian fails | No consolidation | Scratchpad accumulates. Retries next cycle. Memory files never left partial. |

**Rate limiting:** Each channel has a configurable request rate limit (default: 10 requests/minute). Requests exceeding the limit are queued, not dropped. This prevents a compromised or misbehaving channel from burning Claude API budget. The cost guardrails (Section 17) apply as a second line of defense.

---

## 16. Onboarding

First-run experience when Alfred is deployed.

### Steps

1. **Voice enrollment** — Record 5-10 phrases for speaker ID fingerprint (SpeechBrain)
2. **Signal linking** — Pair phone number with Signal bridge
3. **Web PWA setup** — Create account, enroll WebAuthn credential (Face ID / Touch ID)
4. **Integration auth** — Connect Apple Calendar (CalDAV credentials), Apple Health (configure iOS export), Robinhood (API auth), weather (location)
5. **Basic preference seeding** — Conversational: "What time do you usually wake up?", "What's your work address?", "Any dietary restrictions?" Seeds semantic memory with starter facts.
6. **Proactivity level selection** — Opinionated / moderate / conservative
7. **Guest mode configuration** — What smart home controls guests are allowed

### Learning Over Time

Onboarding seeds the minimum. Alfred learns the rest:
- Work location from phone GPS patterns over time
- Sleep preferences from HealthKit correlation
- Communication style from conversation patterns
- Routines from observed behavior → procedural memory

---

## 17. Cost Guardrails

Claude API costs must be visible and controllable.

### Controls

- **Daily spend cap** with alerts — configurable, default $5/day
- **Token budget per request category:**
  - Briefing: up to 4K output tokens
  - Conversation turn: up to 2K output tokens
  - Librarian consolidation: up to 8K output tokens
- **Monthly spend tracking** in SigNoz dashboard
- **Alert at 80% of daily cap** via Signal: "Sir, we've used $4 of the $5 daily budget."
- **At cap:** Alfred switches to System 1 only for the rest of the day, unless overridden

### Cost Optimization

- Simple requests fast-path to System 1 (local, free)
- Context window management trims low-priority memory to reduce prompt tokens
- Integration data is summarized before inclusion in prompts
- Librarian runs once daily, not on every interaction

---

## 18. "Good Morning" Demo — First Deliverable

Ties the entire system together as proof of concept.

### Scenario

User says "Good morning" via push-to-talk on web PWA or texts it on Signal.

### Flow

```
1. "Good morning" → STT (if voice) → UserRequest
2. Identity Gate → confirmed as sir
3. Conscious Engine recognizes greeting + time-of-day = morning briefing
4. Context assembly (parallel):
   ├── apple_calendar  → today's events, first meeting time
   ├── apple_health    → last night's sleep data
   ├── weather         → current + forecast
   ├── robinhood       → portfolio overnight change
   ├── ContextReader   → overnight HA events
   ├── Episodic memory → anything notable from yesterday
   └── Semantic memory → preferences (commute, briefing style)
5. Claude assembles briefing with personality
6. Response → TTS (if voice) → back to channel
7. Scratchpad write with observations
```

### Example Response

> Good morning, sir. You managed 6 hours and 12 minutes of sleep, though only 48 minutes of deep sleep — below your usual. I'd recommend against the late espresso.
>
> Your first engagement is at 10 AM — a standup with the platform team. Commute looks clear, 22 minutes.
>
> It's 58 degrees and sunny, warming to 74 by afternoon. No rain.
>
> Your portfolio is up 0.3% overnight. Tesla recovered modestly.
>
> The front door sensor triggered at 4:12 AM — likely the wind. I've noted it but saw nothing concerning.
>
> Shall I prepare anything else?

### Guest Version

> Good evening. I'm Alfred. It's 58 degrees and sunny today, warming to 74 by afternoon. Quite pleasant. Would you like me to adjust the lighting or put on some music?

Same personality, zero personal data.

### What This Proves

- Conscious Engine (Claude-powered reasoning with personality)
- Integration Registry (4 adapters called in parallel)
- Memory (episodic recall, semantic preferences, scratchpad write)
- Identity Gate (full briefing for sir, limited for guest)
- Channel agnostic (same flow for voice or Signal)
- Observability (full trace waterfall in SigNoz)
- Voice pipeline (STT → engine → TTS, streaming, ~1.5s to first word)

### On-Deck Demos (Future)

- **Departure routine:** "I'm heading out." → Lock door, set thermostat, arm security, summary on Signal.
- **Conversational task:** "Draft a reply to that email" / "Research X for me" / "Plan my evening."

---

## 19. Phased Delivery

Completed phases remain as-is. This spec covers Phase 3 onward.

### Phase 1: Foundation — COMPLETE

Event bus, Reflex Engine, home-service, SDK, telemetry skeleton, preferences.

### Phase 2: Triggers + Evals — COMPLETE

Trigger Engine, ContextProvider, evals runner, BaseFeature, trigger hardening.

### Phase 3: The Brain — THIS SPEC

**Step 0 — Prerequisites (resolve existing backlog):**
- Loguru setup in `shared/logging.py` — replaces all `logging.basicConfig()` across entry points in a single PR (resolves trigger-engine-simplification backlog item 6). Every subsequent component benefits.
- Generalize `TraceRecord` in `shared/tracing.py` — decouple from `StateChangedEvent`. Use a `BaseEvent` union or split into `ReflexTraceRecord` / `ConsciousTraceRecord` sharing a common base. Required before System 2 tracing.
- Resolve `ContextReader` hardcoded `service_name="home-service"` — implement multi-service `SCAN alfred:context:*` (resolves context-provider backlog). Required before Conscious Engine context assembly.
- Add all new Redis stream constants to `shared/streams.py` — before any consuming code.

**Step 1 — Domain routing + observability:**
- Generic `DomainRouter` — replaces hardcoded `HomeAgent` in `runner.py`. Must be first because both Reflex and Conscious Engine depend on it. `router.register("home-service", home_agent)` where the string key matches `ToolInfo.target_service` from the tool registry (no magic strings).
- OpenTelemetry SDK integration, `@traced` decorator
- SigNoz deployment on CachyOS server
- Span propagation through Redis + WebSocket

**Step 2 — Conscious Engine core:**
- `core/conscious/engine.py` — Claude-powered reasoning loop with agentic loop (multi-step tool use)
- `UserRequest` / `AlfredResponse` schemas (extending `BaseEvent`)
- Identity gate (device/session-based first, voice ID later)
- WebSocket transport for real-time, Redis async audit logging
- System 1 ↔ System 2 coexistence (static event routing table)
- Cost tracking (`CostState` model, budget checks before Claude calls)

**Step 3 — Memory expansion:**
- Episodic memory (Redis hot + SQLite cold via `sqlite-vec`, with startup fallback to full-table-scan if extension unavailable)
- Embedding model integration (local sentence-transformer, embeddings stored separately from Pydantic models)
- Semantic memory (extend preferences + new profile/)
- Procedural memory (routines with `RoutineStep` containing both description and `ActionPayload` for trigger promotion)
- Librarian upgrade (consolidation + decay + pattern detection + unlearning)
- **Librarian must use atomic file writes:** write to `.tmp` then `os.rename()` to prevent read/write races with the Conscious Engine reading semantic memory concurrently

**Step 4 — Integration Registry + first adapters:**
- `IntegrationRegistry` with ABC base class
- `apple_calendar.py`, `weather.py` (easiest two)
- `apple_health.py`, `robinhood.py` (need bridge/auth work)
- Response sanitization layer

**Step 5 — Interaction channels:**
- Signal bridge (sovereign service, signal-cli)
- Web PWA (push-to-talk + chat, WebSocket to engine)
- Voice pipeline (Whisper STT + Piper TTS, streaming)
- Onboarding flow

**Step 6 — Evals expansion:**
- DeepEval integration for System 2 quality metrics
- Custom metrics (personality, privacy, proactivity, memory precision)
- Existing evals runner gets regression mode (mocked Ollama)
- End-to-end scenario simulation with mocked integrations

**Step 7 — First demo: "Good morning" briefing:**
- Full end-to-end on both voice and Signal
- Full trace visible in SigNoz
- Personality, memory, integrations all working

### Phase 4: Autonomous Learning — FUTURE

- Librarian running nightly in production
- Procedural memory actively suggesting routines
- Proactivity tuning (three levels operational)
- Departure routine demo, conversational task demo
- System prompt refinement from eval feedback

### Phase 5: Scale & Polish — FUTURE

- Additional integrations (iMessage, media/Emby, finance depth)
- Additional domains (media, security, chores, productivity)
- Native iOS/macOS clients
- Wake word support
- Distributed compute across CachyOS + MacBook
- Patent filing + paper submission

---

## 20. Technology Stack (Updated)

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Language | Python 3.13+, async-first | Ecosystem, LLM tooling |
| Schemas | Pydantic v2 | Deterministic comms (Pillar 3) |
| Event Bus (edge) | MQTT / Mosquitto | Native HA/IoT protocol |
| Event Bus (internal) | Redis Streams | Consumer groups, replay, persistence |
| Local SLM | Ollama (configurable model) | GPU-accelerated, System 1 |
| Cloud LLM | Claude (Anthropic API) | System 2 reasoning |
| Orchestration | MCP (Model Context Protocol) | Standardized tool interface |
| Observability | OpenTelemetry → SigNoz | Distributed tracing, metrics, logs |
| Logging | Loguru | Structured logging, context propagation |
| Containers | OCI Containerfiles, Docker Compose | Per-service containers |
| Memory | Markdown + YAML + Redis + SQLite | Human-readable preferences, fast episodic, cold archive |
| Research | Obsidian vault (symlinked) | Browsable, interconnected notes |
| STT | Whisper (whisper.cpp, large-v3-turbo) | Local, fast, accurate |
| TTS | Piper (local, upgradeable) | Local-first, streaming capable |
| Speaker ID | SpeechBrain (ECAPA-TDNN) | Best open-source accuracy, on-device |
| Evals | DeepEval + custom runner | Free, pytest-native, custom metrics |
| Web Client | Svelte or vanilla JS (PWA) | Lightweight, responsive |
| Chat Bridge | signal-cli | Open-source, self-hosted |
| Embeddings | sentence-transformers (local) | Episodic memory retrieval |
| Vector Search | sqlite-vec | Cold episodic storage |

### Dependency Notes

- **SpeechBrain + torch** are heavy dependencies (~2GB). They must be optional in `pyproject.toml`: `uv pip install "alfred[voice]"`. The core system runs without them — only the voice pipeline requires torch.
- **signal-cli** requires JRE 17+. The `signal-bridge/` Containerfile must install it explicitly.
- **sqlite-vec** requires `sqlite3.enable_load_extension(True)`. The implementation must verify availability at startup and fall back to full-table-scan cosine similarity if the extension is unavailable, with a warning log.
- **Loguru** may need `types-loguru` for `mypy --strict` compatibility. Verify stubs are available or add targeted `# type: ignore` with comments.
- **DeepEval** is Apache 2.0 as of this writing. Verify current license before committing. The custom metrics (`ButlerPersonalityScore`, `PrivacyLeakScore`) can be reimplemented as standalone pytest fixtures if the license changes.

---

## 21. Key Constraints & Decisions

All constraints from the original spec remain. Additional:

- **Personality is not hardcoded in source** — stored in `core/conscious/prompts/personality.md`, versioned, editable.
- **No hardcoded integration lists** — IntegrationRegistry discovers adapters dynamically, same as ToolRegistry.
- **Raw data stays in source systems** — Alfred stores inferences and preferences only. User can inspect/edit/delete.
- **Privacy allowlist** — Alfred only sends data to explicitly approved cloud services.
- **Voice ID is one factor, not the only factor** — layered auth by action risk.
- **Guest mode preserves personality** — same butler, different information access.
- **Streaming for voice** — WebSocket hot path, not queued. Time-to-first-word target: 1.5s.
- **Cost visibility** — daily cap, per-category budgets, 80% alerts, automatic System 1 fallback at cap.
- **Graceful degradation** — every component failure has a defined fallback. Alfred never goes fully silent.
- **Atomic file writes for memory** — Librarian and any memory writer must write to `.tmp` then `os.rename()`. POSIX `rename()` is atomic, preventing read/write races with the Conscious Engine reading semantic memory concurrently.
- **Long-lived `httpx.AsyncClient`** — one client per service, never per-request. Follows `HomeAgent` precedent.
- **Import shared utilities** — `AioRedis` type alias from `core.reflex.runner`, `ensure_consumer_group` from same, stream constants from `shared.streams`. Never redefine or reimplement.
- **`@track_latency` on all inference entry points** — `conscious_engine.process_request()`, each integration `execute()`, STT, TTS. Follows `process_event()` precedent.
- **Registry decorator pattern** — all new registries (`IntegrationRegistry`, `DomainRouter`) must use the `@Registry.register()` class method decorator pattern established by `TriggerRegistry`.

---

## 22. New Schemas (Pillar 3 Compliance)

All new Pydantic models introduced by this spec. These live in `bus/schemas/` (inter-service) or their owning module (internal).

### Inter-Service Schemas (`bus/schemas/events.py`)

```python
class UserRequest(BaseEvent):
    """Inbound user interaction from any channel. Extends BaseEvent for bus compatibility."""
    event_type: str = "user_request"
    channel: Literal["web_pwa", "signal", "voice"]
    session_id: str
    identity_claim: str          # "sir", "guest", or provider-specific token
    content_type: Literal["text", "audio"]
    content: str                 # Transcribed text (if audio, after STT)
    audio_ref: str | None = None # Object store ref for raw audio (if voice)
    # Inherits event_id, timestamp, source from BaseEvent

class AlfredResponse(BaseEvent):
    """Outbound response to a user channel. Extends BaseEvent for bus compatibility."""
    event_type: str = "alfred_response"
    channel: Literal["web_pwa", "signal", "voice"]
    session_id: str
    text: str
    voice_audio_ref: str | None = None  # Ref to TTS audio file/object (not raw bytes — avoids Redis bloat)
    actions_taken: list[str]            # Summary of actions executed
    mood: Literal["neutral", "pleased", "concerned", "amused", "serious"]
    # Inherits event_id, timestamp, source from BaseEvent
```

**Note:** `UserRequest` and `AlfredResponse` extend `BaseEvent` (not `BaseModel`) to follow the existing convention for all inter-service schemas. This ensures they carry `event_id` for trace correlation and can flow through the event bus. `voice_audio_ref` is a reference (file path or object store key), not raw bytes — channel services fetch audio from the ref. This avoids multi-MB blobs in Redis streams.

### Memory Schemas (`core/memory/schemas.py`)

```python
class EpisodicEntry(BaseModel):
    """Episodic memory entry. Embedding stored separately (keyed by id) to avoid
    base64 bloat in JSON serialization. See core/memory/embeddings.py."""
    id: str
    timestamp: datetime
    source: str                    # "conversation", "system1_action", "trigger", "integration"
    summary: str
    entities: list[str]            # Entity IDs or names referenced
    valence: Literal["positive", "negative", "neutral"]
    # NOTE: embedding is NOT in this model. Stored separately as raw bytes
    # in Redis (binary field keyed by id) and SQLite (BLOB column).
    # The retrieval layer joins entry + embedding at query time.

class RoutineStep(BaseModel):
    """A single step in a learned routine."""
    description: str               # Human-readable ("dim the living room lights to 30%")
    action: ActionPayload | None = None  # Machine-executable, populated at promotion time
    # ActionPayload is from core/triggers/models.py — reused for trigger promotion

class RoutineSpec(BaseModel):
    name: str
    trigger_pattern: str           # Natural language description of when this fires
    steps: list[RoutineStep]       # Ordered steps with description + optional action payload
    confidence: float              # 0.0-1.0
    learned_from: list[str]        # Episodic entry IDs that contributed
    state: Literal["candidate", "active", "dormant", "archived"]
    last_hit: datetime | None = None
    consecutive_misses: int = 0

class CostState(BaseModel):
    """Daily Claude API spend tracking. Stored at alfred:cost:daily in Redis."""
    date: str                      # ISO date (YYYY-MM-DD)
    spend_usd: float               # Accumulated spend today
    cap_usd: float                 # Daily cap from config
    alert_sent: bool = False       # True if 80% alert was sent
```

### Integration Schemas (`core/integrations/base.py`)

`IntegrationRequest`, `IntegrationResult`, `IntegrationCapability` — defined in Section 7.

### Identity Schemas (`core/identity/schemas.py`)

```python
class IdentityResult(BaseModel):
    identity: Literal["sir", "guest"]
    confidence: float              # 0.0-1.0
    method: str                    # "voice_id", "signal_phone", "webauthn", "device_proximity"
    factors: list[str]             # All factors that contributed
    risk_clearance: Literal["low", "medium", "high", "critical"]
```

---

## 23. New Redis Streams & Keys

All new stream names and keys to be added to `shared/streams.py`.

| Key | Type | Purpose |
|-----|------|---------|
| `alfred:user:requests` | Stream | Inbound user interactions (all channels) |
| `alfred:user:responses` | Stream | Outbound Alfred responses (all channels) |
| `alfred:memory:episodic` | Stream | Hot episodic memory entries (last 7 days) |
| `alfred:memory:scratchpad` | List | Existing scratchpad queue (unchanged) |
| `alfred:sessions:{session_id}` | Hash | Active conversation session state |
| `alfred:identity:voiceprint` | Hash | Enrolled speaker embeddings |
| `alfred:config:runtime` | Hash | Runtime-tunable settings (proactivity level, DND, cost cap) |
| `alfred:integration_registry` | Hash | Integration manifests (mirrors tool_registry pattern) |
| `alfred:notifications:queue` | Stream | Outbound proactive notifications |
| `alfred:cost:daily` | Hash | Daily Claude API spend tracking (`CostState`) |
