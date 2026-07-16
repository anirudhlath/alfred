# Web Asset Cache Headers: Immutable for Hashed /assets/*

## Summary

Vite produces content-hashed asset filenames (e.g. `/assets/index-Bx3kYp9z.js`). These
filenames change on every build when the file content changes, so they are safe to cache
immutably. Currently `NoCacheStaticMiddleware` applies `Cache-Control: no-cache` to all
static file responses, negating the benefit of hashing.

## Context / Motivation

The SPA is served by `mount_spa()` in `core/channels/spa.py`, which mounts `/assets` via
FastAPI `StaticFiles`. A separate `NoCacheStaticMiddleware` (or equivalent header
injection) currently prevents browsers from caching any assets across page loads.

For `/assets/*` paths, immutable caching is both safe and beneficial:
- Hashed filenames guarantee freshness — a new build generates new URLs.
- `Cache-Control: public, max-age=31536000, immutable` would eliminate repeat network
  requests for returning users.
- Non-hashed paths (`index.html`, public assets) should remain `no-cache` or
  `no-store` to ensure users always get the latest entry point.

## Acceptance Criteria

- `GET /assets/*` responses include `Cache-Control: public, max-age=31536000, immutable`.
- `GET /index.html` and `GET /` responses retain `Cache-Control: no-cache, no-store`.
- `GET /public/*` (non-hashed public assets, if any) retain short or no-cache headers.
- Verified by inspecting response headers in DevTools after `npm run build` + container
  build.
- Existing tests pass; no regressions in auth or SPA routing.
