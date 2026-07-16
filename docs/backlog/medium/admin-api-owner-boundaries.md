# Admin API: respect owning-process boundaries

**Priority:** medium
**Source:** PR #21 review (code-architect + multi-agent review)

## Summary
The `/api/admin/*` surface reaches past the bus/owner boundary in three places. The
trigger-mutation path (via `ACTIONS_STREAM` → `triggers-internal`) is the correct
model; these should follow it.

## Items

### 1. Episodic reads bypass the memory owner
`core/channels/admin_api.py` `memory_episodic`:
- The `?q` path instantiates a full `EpisodicMemory` **with a 300M-param
  `SentenceTransformerProvider`** inside the web process — a second embedder copy
  (the conscious process already holds one) loaded on the event loop that serves
  chat/voice WS.
- The browse path opens `episodic_cold.db` via raw `aiosqlite` with hand-written SQL
  (`SELECT ... FROM episodic_entries`), duplicating the cold-store schema across
  packages (silent break when `SqliteVecStore` migrates its schema) and adding a
  second cross-process SQLite connection (risk of `database is locked`).

**Acceptance:** either (a) route episodic reads through the conscious process
(`ActionRequest(target_service="conscious-engine", tool_name="recall_episodic")` +
response stream), mirroring the trigger-mutation pattern; or at minimum (b) move the
cold-store query into a public `SqliteVecStore.list_recent(limit)` method so the
schema stays encapsulated, and gate the embedder load behind an explicit flag.

### 2. DND mutations bypass the dispatcher
`admin_api.py` `set_dnd` writes `DND_STATE_KEY` directly and hand-builds the DND JSON
shape. `DNDChecker`/`NotificationDispatcher` own DND semantics — notably scheduling a
deferred-notification **drain when DND clears**. A direct write diverges from that
invariant (clearing DND from the web app won't trigger the drain a dispatcher-routed
clear would) and authors the DND JSON shape in two places.

**Acceptance:** route DND mutations through the conscious process (like `run_librarian`
/ `drain_deferred_notifications`), so the "clear ⇒ schedule drain" invariant and the
DND JSON shape live in one owner. `delete_session` (pure ephemeral state) may stay a
direct `r.delete`.

### 3. Auth router registered inside the lifespan (SPA ordering hazard)
`core/channels/web_server.py` registers the auth router and then `mount_spa` inside the
lifespan, because the SPA catch-all must register **after** the API routers. This
ordering is load-bearing, self-documented as untestable (`web/dist/` absent in CI so
the mount is a no-op), and any future router added after this point is silently
shadowed by the SPA fallback in production only.

**Acceptance:** register all API routers before a single `mount_spa` at the end of
`create_app` (read `redis`/`credential_store` lazily from `app.state` in handlers, as
`AuthCookieMiddleware` already does), removing the reason the ordering hazard exists;
or add a startup assertion that fails if any `/api/*` route is registered after the
catch-all.
