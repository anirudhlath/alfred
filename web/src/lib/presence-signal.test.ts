import { describe, expect, it } from "vitest";
import { PresenceSignal } from "./presence-signal";

/** An AnalyserNode stand-in that reports a fixed spectrum. */
function fakeAnalyser(fill: number, bins = 128): AnalyserNode {
  return {
    frequencyBinCount: bins,
    getByteFrequencyData: (array: Uint8Array) => array.fill(fill),
  } as unknown as AnalyserNode;
}

/** Run `count` ticks at 60 fps starting from `from`, returning the last frame. */
function run(signal: PresenceSignal, count: number, from = 0) {
  let frame = signal.tick(from);
  for (let i = 1; i <= count; i++) frame = signal.tick(from + i / 60);
  return frame;
}

describe("PresenceSignal at rest", () => {
  it("is perfectly still", () => {
    const signal = new PresenceSignal();
    const frame = run(signal, 120);
    expect(frame.level).toBe(0);
    expect(frame.think).toBe(0);
    expect(frame.bands).toHaveLength(8);
    expect(frame.bands.every((band) => band === 0)).toBe(true);
  });
});

describe("PresenceSignal while holding", () => {
  it("synthesizes a talking envelope with no analyser attached", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    const frame = run(signal, 60);
    expect(frame.level).toBeGreaterThan(0);
    expect(frame.level).toBeLessThanOrEqual(1);
  });

  it("keeps every band inside the unit range", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    for (let i = 0; i < 300; i++) {
      const frame = signal.tick(i / 60);
      for (const band of frame.bands) {
        expect(Number.isFinite(band)).toBe(true);
        expect(band).toBeGreaterThanOrEqual(0);
        expect(band).toBeLessThanOrEqual(1);
      }
    }
  });

  it("rises faster than it falls", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    // Attack 0.35 per tick: the first frame of speech already carries a third
    // of the way to the target...
    const afterOneRise = signal.tick(0).level;
    expect(afterOneRise).toBeGreaterThan(0.3);

    signal.setHolding(false);
    // ...and decay 0.06: one frame of silence must not undo it.
    expect(signal.tick(1 / 60).level).toBeGreaterThan(afterOneRise * 0.9);
  });

  it("decays to a hard zero once released", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    run(signal, 60);
    signal.setHolding(false);
    const frame = run(signal, 400, 1);
    expect(frame.level).toBe(0);
  });
});

describe("PresenceSignal with an analyser", () => {
  it("reads the live spectrum instead of synthesizing one", () => {
    const signal = new PresenceSignal();
    signal.attach(fakeAnalyser(200));
    signal.setHolding(true);
    const frame = run(signal, 60);
    expect(frame.level).toBeGreaterThan(0.5);
  });

  it("survives the zero-width top band that fftSize 256 produces", () => {
    // frequencyBinCount 128 makes band 7 empty (lo 128, hi 128). The prototype
    // divides by that zero and turns the whole field into NaN.
    const signal = new PresenceSignal();
    signal.attach(fakeAnalyser(255, 128));
    signal.setHolding(true);
    const frame = run(signal, 60);
    expect(Number.isNaN(frame.level)).toBe(false);
    expect(frame.bands.every((band) => Number.isFinite(band))).toBe(true);
  });

  it("goes back to the synthesized envelope when the analyser is detached", () => {
    const signal = new PresenceSignal();
    signal.attach(fakeAnalyser(255));
    signal.setHolding(true);
    run(signal, 30);

    signal.attach(null);
    const frame = run(signal, 30, 1);

    expect(Number.isFinite(frame.level)).toBe(true);
    expect(frame.level).toBeGreaterThan(0);
  });

  it("ignores the analyser when not holding", () => {
    const signal = new PresenceSignal();
    signal.attach(fakeAnalyser(255));
    const frame = run(signal, 200);
    expect(frame.level).toBe(0);
  });
});

describe("PresenceSignal while thinking", () => {
  it("eases in and rises monotonically", () => {
    const signal = new PresenceSignal();
    signal.setThinking(true);
    let previous = -1;
    for (let i = 0; i < 90; i++) {
      const frame = signal.tick(i / 60);
      expect(frame.think).toBeGreaterThanOrEqual(previous);
      previous = frame.think;
    }
    expect(previous).toBeGreaterThan(0.4);
    expect(previous).toBeLessThan(1);
  });

  it("eases out more slowly than it eased in", () => {
    const signal = new PresenceSignal();
    signal.setThinking(true);
    const peak = run(signal, 90).think;

    signal.setThinking(false);
    const afterTen = run(signal, 10, 2).think;

    // In 0.08, out 0.035 — the field settles rather than snaps.
    expect(afterTen).toBeGreaterThan(peak * 0.6);
  });

  it("settles to a hard zero once the thought is done", () => {
    const signal = new PresenceSignal();
    signal.setThinking(true);
    run(signal, 90);
    signal.setThinking(false);
    expect(run(signal, 400, 2).think).toBe(0);
  });

  it("is independent of the audio level", () => {
    const signal = new PresenceSignal();
    signal.setThinking(true);
    const frame = run(signal, 60);
    expect(frame.level).toBe(0);
    expect(frame.think).toBeGreaterThan(0);
  });
});

describe("PresenceSignal offline", () => {
  it("hands out a flat field however loud it is", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    signal.setThinking(true);
    run(signal, 60);

    signal.setOffline(true);
    const frame = signal.tick(2);

    expect(frame.level).toBe(0);
    expect(frame.think).toBe(0);
    expect(frame.bands.every((band) => band === 0)).toBe(true);
  });

  it("comes back without a jump, because the envelopes kept running", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    signal.setOffline(true);
    run(signal, 60);

    signal.setOffline(false);
    const frame = signal.tick(2);

    expect(frame.level).toBeGreaterThan(0);
  });
});
