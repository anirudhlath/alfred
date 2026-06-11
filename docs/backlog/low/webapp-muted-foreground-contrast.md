# Muted Foreground Contrast Below AA-Normal Target

## Summary

`--muted-foreground` (#64748b) measures 4.0–4.1:1 contrast ratio on the Mission Control backgrounds (#090c12 / #0b101a). This clears the WCAG AA floor for large text but falls below the 4.5:1 AA-normal target required for the small monospace timestamps it styles.

## Context / Motivation

The design is approved as-is and ships with the web app rebuild. The contrast gap is small and affects only low-salience UI elements (timestamps, secondary labels). No blocking issue was filed during review; this is logged for future polish.

Suggested fix when revisiting: nudge `--muted-foreground` to approximately #7a8699, which clears 4.5:1 on both background values while staying visually consistent with the dark Mission Control palette.

## Acceptance Criteria

- `--muted-foreground` achieves ≥ 4.5:1 contrast ratio against #090c12 and #0b101a (verify with a WCAG contrast checker)
- Monospace timestamps and all other elements using `text-muted-foreground` pass WCAG AA-normal
- Visual design review confirms the updated tint remains consistent with the Mission Control aesthetic
