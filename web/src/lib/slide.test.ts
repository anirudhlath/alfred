import { describe, expect, it } from "vitest";
import { CONFIRM_RATIO, hintOpacity, slideKnob } from "./slide";

/** 393 pt phone, 20 px gutters, 64 px of track taken by the knob and its inset. */
const MAX = 289;

describe("CONFIRM_RATIO", () => {
  it("is 85% of the travel", () => {
    expect(CONFIRM_RATIO).toBe(0.85);
  });
});

describe("slideKnob", () => {
  it("starts at home", () => {
    expect(slideKnob(0, MAX)).toBe(0);
  });

  it("resists for the first quarter, at 60% of the finger", () => {
    expect(slideKnob(MAX * 0.1, MAX)).toBeCloseTo(MAX * 0.06, 5);
    expect(slideKnob(MAX * 0.25, MAX)).toBeCloseTo(MAX * 0.15, 5);
  });

  it("catches up after the resistance", () => {
    // 0.15 + (0.5 - 0.25) * 1.1333
    expect(slideKnob(MAX * 0.5, MAX)).toBeCloseTo(MAX * 0.433325, 4);
  });

  it("lands under the finger at the far end", () => {
    expect(slideKnob(MAX, MAX)).toBeCloseTo(MAX, 1);
  });

  it("never goes backwards or past the end", () => {
    expect(slideKnob(-200, MAX)).toBe(0);
    expect(slideKnob(MAX * 3, MAX)).toBeLessThanOrEqual(MAX);
  });

  it("crosses the confirm threshold only after a deliberate drag", () => {
    expect(slideKnob(MAX * 0.86, MAX)).toBeLessThan(MAX * CONFIRM_RATIO);
    expect(slideKnob(MAX * 0.88, MAX)).toBeGreaterThan(MAX * CONFIRM_RATIO);
  });

  it("is zero on a track with no travel", () => {
    expect(slideKnob(120, 0)).toBe(0);
  });
});

describe("hintOpacity", () => {
  it("fades the hint out over the first half of the travel", () => {
    expect(hintOpacity(0, MAX)).toBe(1);
    expect(hintOpacity(MAX * 0.25, MAX)).toBeCloseTo(0.5, 5);
    expect(hintOpacity(MAX * 0.5, MAX)).toBe(0);
  });

  it("never goes negative", () => {
    expect(hintOpacity(MAX, MAX)).toBe(0);
  });

  it("is fully visible before the track has been measured", () => {
    expect(hintOpacity(0, 0)).toBe(1);
  });
});
