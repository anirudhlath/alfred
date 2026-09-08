import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PresenceSignal } from "@/lib/presence-signal";
import { THEME_KEY } from "@/lib/theme";
import { ThemeProvider } from "@/shell/ThemeProvider";
import { PresenceField } from "./PresenceField";

/** 12 pt dot grid, W 393 / H 190: 34 columns x 17 rows. */
const STEP = 12;
const DOTS = 34 * 17;

interface Recorded {
  /** Every dot drawn, as `[x, y, radius]`. */
  arcs: Array<[number, number, number]>;
  ellipses: number;
  fillStyles: string[];
  cleared: number;
}

let recorded: Recorded;

function fakeContext(): CanvasRenderingContext2D {
  const ctx = {
    setTransform: () => {},
    clearRect: () => void recorded.cleared++,
    beginPath: () => {},
    arc: (x: number, y: number, r: number) => void recorded.arcs.push([x, y, r]),
    ellipse: () => void recorded.ellipses++,
    fill: () => {},
    set fillStyle(value: string) {
      recorded.fillStyles.push(value);
    },
    get fillStyle() {
      return recorded.fillStyles.at(-1) ?? "";
    },
  };
  return ctx as unknown as CanvasRenderingContext2D;
}

/** Stub the media query; the returned switch flips it while the field is mounted. */
function stubReducedMotion(matches: boolean) {
  const listeners = new Set<() => void>();
  const query = {
    matches,
    media: "(prefers-reduced-motion: reduce)",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: (_type: string, listener: () => void) => void listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => void listeners.delete(listener),
    dispatchEvent: () => false,
  };
  vi.stubGlobal("matchMedia", () => query);
  return {
    flip(next: boolean) {
      query.matches = next;
      act(() => listeners.forEach((listener) => listener()));
    },
    listening: () => listeners.size,
  };
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, "hidden", { configurable: true, value: hidden });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: hidden ? "hidden" : "visible",
  });
}

function renderField(signal: PresenceSignal, offline = false) {
  return render(
    <ThemeProvider>
      <PresenceField signal={signal} offline={offline} />
    </ThemeProvider>,
  );
}

beforeEach(() => {
  recorded = { arcs: [], ellipses: 0, fillStyles: [], cleared: 0 };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    fakeContext() as unknown as never,
  );
  setHidden(false);
  // Pin the theme: with no stored choice, resolveInitialTheme picks light
  // during the day, and the colour assertions below are the dark triples.
  localStorage.setItem(THEME_KEY, "dark");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("PresenceField", () => {
  it("is a 393x190 canvas that takes no pointer events", () => {
    const { container } = renderField(new PresenceSignal());
    const canvas = container.querySelector("canvas")!;
    expect(canvas).toBeInTheDocument();
    expect(canvas).toHaveAttribute("aria-hidden", "true");
    expect(canvas.style.width).toBe("393px");
    expect(canvas.style.height).toBe("190px");
    expect(canvas.style.pointerEvents).toBe("none");
  });

  it("scales the backing store by the device pixel ratio, capped at 2", () => {
    vi.stubGlobal("devicePixelRatio", 3);
    const { container } = renderField(new PresenceSignal());
    const canvas = container.querySelector("canvas")!;
    expect(canvas.width).toBe(786);
    expect(canvas.height).toBe(380);
  });

  it("draws the whole grid on its first frame", () => {
    renderField(new PresenceSignal());
    expect(recorded.cleared).toBeGreaterThanOrEqual(1);
    expect(recorded.arcs.length).toBeGreaterThanOrEqual(DOTS);
  });

  it("paints a resting field once, and leaves it alone until something moves", () => {
    const pending: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => pending.push(cb));
    const signal = new PresenceSignal();
    renderField(signal);
    expect(recorded.cleared).toBe(1);

    // Two more frames at rest: the loop keeps running, the canvas is not touched.
    pending.shift()!(16);
    pending.shift()!(32);
    expect(recorded.cleared).toBe(1);

    // Speech arrives: the next frame paints again.
    signal.setHolding(true);
    pending.shift()!(48);
    expect(recorded.cleared).toBe(2);
  });

  it("draws in amber when connected and in grey when not (dark theme)", () => {
    renderField(new PresenceSignal(), false);
    expect(recorded.fillStyles.some((s) => s.startsWith("rgba(232,178,132"))).toBe(true);

    recorded = { arcs: [], ellipses: 0, fillStyles: [], cleared: 0 };
    renderField(new PresenceSignal(), true);
    expect(recorded.fillStyles.some((s) => s.startsWith("rgba(154,145,134"))).toBe(true);
  });

  it("tells the signal it is offline", () => {
    const signal = new PresenceSignal();
    const setOffline = vi.spyOn(signal, "setOffline");
    renderField(signal, true);
    expect(setOffline).toHaveBeenCalledWith(true);
  });

  it("draws exactly one static frame under reduce-motion", () => {
    stubReducedMotion(true);
    const raf = vi.spyOn(window, "requestAnimationFrame");

    renderField(new PresenceSignal());

    expect(recorded.arcs).toHaveLength(DOTS);
    expect(raf).not.toHaveBeenCalled();
  });

  it("freezes a talking, thinking field flat under reduce-motion", () => {
    stubReducedMotion(true);
    const signal = new PresenceSignal();
    signal.setHolding(true);
    signal.setThinking(true);
    // Warm the envelopes: a frame ticked from here would be anything but flat.
    for (let i = 0; i < 60; i++) signal.tick(i / 60);

    renderField(signal);

    // Every dot on its grid point at its resting radius, and no shadows.
    expect(recorded.arcs).toHaveLength(DOTS);
    for (const [x, y, r] of recorded.arcs) {
      expect(x % STEP).toBe(0);
      expect(y % STEP).toBe(0);
      expect(r).toBe(1);
    }
    expect(recorded.ellipses).toBe(0);
  });

  it("freezes when reduce-motion is switched on mid-session", () => {
    const media = stubReducedMotion(false);
    const raf = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    const cancel = vi.spyOn(window, "cancelAnimationFrame");
    const { unmount } = renderField(new PresenceSignal());
    expect(raf).toHaveBeenCalledTimes(1);

    media.flip(true);

    expect(cancel).toHaveBeenCalled();
    expect(raf).toHaveBeenCalledTimes(1);
    expect(recorded.cleared).toBe(2);

    unmount();
    expect(media.listening()).toBe(0);
  });

  it("does not animate a hidden tab, and paints it at rest", () => {
    setHidden(true);
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const signal = new PresenceSignal();
    signal.setHolding(true);
    for (let i = 0; i < 60; i++) signal.tick(i / 60);

    renderField(signal);

    expect(raf).not.toHaveBeenCalled();
    // Still painted once, so returning to the app never shows an empty canvas —
    // and flat, so what it shows is not a wave caught mid-swell.
    expect(recorded.arcs).toHaveLength(DOTS);
    expect(recorded.arcs.every(([x, y, r]) => x % STEP === 0 && y % STEP === 0 && r === 1)).toBe(true);
  });

  it("stops the loop while the tab is hidden and resumes it once, not twice", () => {
    const raf = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    const cancel = vi.spyOn(window, "cancelAnimationFrame");
    renderField(new PresenceSignal());
    expect(raf).toHaveBeenCalledTimes(1);

    setHidden(true);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(cancel).toHaveBeenCalledTimes(1);

    setHidden(false);
    document.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("visibilitychange"));
    // One new handle for the resume; the second event finds the loop running.
    expect(raf).toHaveBeenCalledTimes(2);
  });

  it("stops the loop when it unmounts", () => {
    const cancel = vi.spyOn(window, "cancelAnimationFrame");
    const { unmount } = renderField(new PresenceSignal());
    unmount();
    expect(cancel).toHaveBeenCalled();
  });

  it("survives a canvas with no 2D context at all", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => {
      throw new Error("Not implemented: HTMLCanvasElement.prototype.getContext");
    });
    expect(() => renderField(new PresenceSignal())).not.toThrow();
  });
});
