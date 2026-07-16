# Web Light Theme

## Summary

The Mission Control SPA is dark-only by design. This ticket tracks adding a light theme
if ever desired, without blocking any current work.

## Context / Motivation

`web/src/index.css` declares a single `:root` block with dark Mission Control tokens.
The comment reads: "Mission Control — single dark theme by design (no light mode, no
switching)." The design decision is intentional and documented.

A light theme would require:
- A second CSS token set (`:root[data-theme="light"]` or a CSS media query variant).
- A theme toggle control (settings page or TopBar).
- Review of all source-color usages to ensure legibility in light context.
- Possibly `next-themes` (already a dependency) integration.

## Acceptance Criteria

- `next-themes` `ThemeProvider` wraps the app with `attribute="data-theme"`.
- A theme toggle (e.g. in TopBar or Settings) allows switching between dark and light.
- All source colors (`reflex`, `conscious`, `memory`, `trigger`, `home`, `user`) have
  light-theme variants with sufficient contrast (WCAG AA).
- The `pulse-dot` animation remains visible in both themes.
- Defaults to dark; respects `prefers-color-scheme` on first visit.
- No regressions in dark theme appearance.
