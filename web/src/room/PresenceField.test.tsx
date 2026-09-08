import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PresenceSignal } from "@/lib/presence-signal";
import { THEME_KEY } from "@/lib/theme";
import { ThemeProvider } from "@/shell/ThemeProvider";
import { PresenceField } from "./PresenceField";

interface Recorded {
  arcs: number;
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
    arc: () => void recorded.arcs++,
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

function stubReducedMotion(matches: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
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
  recorded = { arcs: 0, ellipses: 0, fillStyles: [], cleared: 0 };
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
    // 34 columns x 17 rows, one arc each.
    expect(recorded.cleared).toBeGreaterThanOrEqual(1);
    expect(recorded.arcs).toBeGreaterThanOrEqual(34 * 17);
  });

  it("draws in amber when connected and in grey when not (dark theme)", () => {
    renderField(new PresenceSignal(), false);
    expect(recorded.fillStyles.some((s) => s.startsWith("rgba(232,178,132"))).toBe(true);

    recorded = { arcs: 0, ellipses: 0, fillStyles: [], cleared: 0 };
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

    expect(recorded.arcs).toBe(34 * 17);
    expect(raf).not.toHaveBeenCalled();
  });

  it("does not animate a hidden tab", () => {
    setHidden(true);
    const raf = vi.spyOn(window, "requestAnimationFrame");

    renderField(new PresenceSignal());

    expect(raf).not.toHaveBeenCalled();
    // Still painted once, so returning to the app never shows an empty canvas.
    expect(recorded.arcs).toBe(34 * 17);
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
