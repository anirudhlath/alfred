# Web Activity Page: Virtualized List for Large Feed Volumes

## Summary

The `ActivityPage` renders all entries from the live feed (`FeedEntry[]`) directly into
the DOM. The `FEED_MAX` ring buffer caps the live feed at 500 entries. When a stream
filter is active, backfill entries from `GET /api/admin/streams/{name}?count=100` are
merged in. Beyond ~500 entries, rendering performance degrades noticeably on low-end hardware.

## Context / Motivation

Current rendering path:

```tsx
{entries.map((entry) => (
  <button key={...} ...>...</button>
))}
```

All `entries.length` DOM nodes are mounted simultaneously. At FEED_MAX = 500 with a
busy system, this creates 500 DOM nodes on the Activity page. With backfill merged in,
it could reach 600. Each node contains multiple `<span>` elements.

Symptoms at high volume:
- Visible scroll jitter when new entries arrive (full re-render of list).
- Slow initial mount when navigating to Activity after the system has been running.

## Acceptance Criteria

- Activity page uses a virtualized list (e.g. `@tanstack/react-virtual` or `react-window`)
  so only visible rows are mounted in the DOM.
- `FEED_MAX` can be raised to 2000 without degrading scroll performance.
- Existing pause/filter/inspect behavior is preserved.
- The `#entry.id` URL hash scroll target (used by TelemetryRail links) still works.
- No additional bundle size impact beyond the virtualization library.
- All existing `ActivityPage` tests pass.

## Notes

- The `compareIdsDesc` sort used for backfill merge is compatible with a stable virtualized
  list — no changes needed there.
- The `EventInspector` side panel is not in the scrolling list and does not need
  virtualization.
- Consider also capping the `FEED_MAX` constant as a named export so it can be changed
  without a code audit.
