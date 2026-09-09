/** Release at or past this fraction of the travel and it counts as a yes. */
export const CONFIRM_RATIO = 0.85;

/**
 * Where the knob sits for a finger `dx` px from where it started.
 *
 * Ported from `slideMove` in `Alfred.dc.html`: the knob moves at 60 % of the
 * finger for the first quarter of the track and then catches up at 113 %. That
 * asymmetry is the whole safety argument — a brush against the track goes almost
 * nowhere, and a deliberate drag still ends under the finger.
 */
export function slideKnob(dx: number, max: number): number {
  if (max <= 0) return 0;
  const r = Math.max(0, Math.min(1, dx / max));
  const eased = r < 0.25 ? r * 0.6 : 0.15 + (r - 0.25) * 1.1333;
  return Math.min(max, eased * max);
}

/**
 * "Slide to confirm" fades out over the first half of the travel, so the
 * instruction is gone by the time it would be under the knob.
 */
export function hintOpacity(knob: number, max: number): number {
  if (max <= 0) return 1;
  return Math.max(0, 1 - knob / (max * 0.5));
}
