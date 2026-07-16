# Web frontend follow-ups

**Priority:** low
**Source:** PR #21 review (multi-agent review + code-architect)

## 1. Stream catalog duplicated across the language boundary
The friendly stream list exists three times: `core/channels/stream_catalog.py`
`STREAM_CATALOG` (source of truth) and two hardcoded TS copies —
`web/src/shell/AlfredProvider.tsx` `ALL_STREAMS` and `web/src/pages/ActivityPage.tsx`
`STREAMS`. Adding a 9th stream requires editing two unrelated `.tsx` files or it
silently won't appear in the telemetry rail / Activity filter (no test/type catches
it). **Acceptance:** derive the frontend list from the server (subscribe to whatever
`/api/admin/streams` reports, or expose `GET /api/admin/stream-catalog`), or at minimum
collapse the two TS copies into one exported constant in `web/src/lib/types.ts`.

## 2. Chat transcript resets on route change
The chat message store lives in `useChat`, whose only consumer is `ChatPage`. Navigating
away and back loses the transcript (the socket stays connected in `AlfredProvider`;
notification frames are now handled there, but the message history is still local).
**Acceptance:** lift the chat message store into `AlfredProvider` (or a module-level
store) so the transcript persists across routes; `ChatPage` becomes a view over it.

## 3. `types.ts` hand-mirrors bus schemas
`web/src/lib/types.ts` hand-writes TS interfaces shadowing the Pydantic models / admin
response shapes (`ChatServerMessage`, `TelemetryMessage`, `Trigger`, `Overview`,
`EpisodicEntry`). Largely unavoidable across Python↔TS and partly mitigated with index
signatures, but there is no drift guard. **Acceptance (optional):** add a comment on the
mirrored bus schemas pointing at `types.ts`; consider a `datamodel-code-generator` step
as a future task.

## 4. Bundle size
`npm run build` warns the main chunk is >500KB (575KB / 176KB gzip). **Acceptance:**
consider route-level `import()` code-splitting or raising the warning limit
deliberately.
