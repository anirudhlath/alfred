import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LayoutContext, readLayout, useLayout, useViewportLayout } from "./layout";

const QUERIES: Record<string, (width: number) => boolean> = {
  "(min-width: 760px)": (width) => width >= 760,
  "(min-width: 1100px)": (width) => width >= 1100,
};

/**
 * A viewport this wide behind `matchMedia`, on a screen this size. Like a browser,
 * `resize` fires `change` only on a query whose answer flipped, so a hook listening
 * to just one of the two misses the other's crossing. Any other query throws: the
 * two strings are the contract, and a height query is the bug R4 exists to prevent.
 * The screen defaults to a laptop's, since jsdom's is 0×0 and would answer `phone`
 * before any query was asked.
 *
 * `resize` deliberately fires nothing on `window`, and `changeScreen` fires only
 * that, so each test proves the one listener it is about.
 */
function stubViewport(startWidth: number, screenWidth = 1920, screenHeight = 1080) {
  let width = startWidth;
  const known = Object.keys(QUERIES);
  const listeners = new Map(known.map((query) => [query, new Set<() => void>()]));

  function answer(query: string): boolean {
    const evaluate = QUERIES[query];
    if (!evaluate) throw new Error(`unexpected media query: ${query}`);
    return evaluate(width);
  }

  vi.stubGlobal("screen", { width: screenWidth, height: screenHeight });
  vi.stubGlobal("matchMedia", (query: string) => ({
    media: query,
    get matches() {
      return answer(query);
    },
    addEventListener(type: string, listener: () => void) {
      if (type === "change") listeners.get(query)?.add(listener);
    },
    removeEventListener(type: string, listener: () => void) {
      if (type === "change") listeners.get(query)?.delete(listener);
    },
  }));

  return {
    resize(nextWidth: number) {
      const before = known.map(answer);
      width = nextWidth;
      const flipped = known.filter((query, index) => answer(query) !== before[index]);
      act(() => {
        for (const query of flipped) listeners.get(query)?.forEach((listener) => listener());
      });
    },
    changeScreen(nextScreenWidth: number, nextScreenHeight: number) {
      vi.stubGlobal("screen", { width: nextScreenWidth, height: nextScreenHeight });
      act(() => void window.dispatchEvent(new Event("resize")));
    },
    listening: () => [...listeners.values()].reduce((sum, set) => sum + set.size, 0),
  };
}

function ViewportProbe() {
  return <span>{useViewportLayout()}</span>;
}

function LayoutProbe() {
  return <span>{useLayout()}</span>;
}

describe("readLayout", () => {
  it.each([
    [393, 393, 852, "phone"],
    [759, 1920, 1080, "phone"],
    // A phone on its side, with the screen reported either way round.
    [852, 393, 852, "phone"],
    [852, 852, 393, "phone"],
    [760, 1920, 1080, "sheet-over-bench"],
    [1099, 1920, 1080, "sheet-over-bench"],
    [1100, 1920, 1080, "columns"],
    [1920, 1920, 1080, "columns"],
    // The short side's 500 px, at both breakpoints.
    [1000, 499, 1000, "phone"],
    [1000, 500, 1000, "sheet-over-bench"],
    [1200, 499, 1200, "phone"],
    [1200, 500, 1200, "columns"],
  ])("%i wide on a %i×%i screen gives %s", (width, screenWidth, screenHeight, layout) => {
    stubViewport(width, screenWidth, screenHeight);
    expect(readLayout()).toBe(layout);
  });

  it("keeps a landscape tablet in columns when the keyboard shrinks its viewport", () => {
    stubViewport(1280, 800, 1280);
    vi.stubGlobal("innerHeight", 320);
    expect(readLayout()).toBe("columns");
  });

  it("gives phone when matchMedia is not a function", () => {
    stubViewport(1920);
    vi.stubGlobal("matchMedia", undefined);
    expect(readLayout()).toBe("phone");
  });
});

describe("useViewportLayout", () => {
  it("re-renders when the viewport crosses 760", () => {
    const viewport = stubViewport(759);
    render(<ViewportProbe />);
    expect(screen.getByText("phone")).toBeInTheDocument();

    viewport.resize(760);
    expect(screen.getByText("sheet-over-bench")).toBeInTheDocument();

    viewport.resize(759);
    expect(screen.getByText("phone")).toBeInTheDocument();
  });

  it("re-renders when the viewport crosses 1100", () => {
    const viewport = stubViewport(1099);
    render(<ViewportProbe />);
    expect(screen.getByText("sheet-over-bench")).toBeInTheDocument();

    viewport.resize(1100);
    expect(screen.getByText("columns")).toBeInTheDocument();

    viewport.resize(1099);
    expect(screen.getByText("sheet-over-bench")).toBeInTheDocument();
  });

  it("a foldable unfolding re-reads the screen", () => {
    const viewport = stubViewport(841, 344, 882);
    render(<ViewportProbe />);
    expect(screen.getByText("phone")).toBeInTheDocument();

    viewport.changeScreen(673, 841);
    expect(screen.getByText("sheet-over-bench")).toBeInTheDocument();
  });

  it("a foldable folding back re-reads the screen", () => {
    const viewport = stubViewport(841, 673, 841);
    render(<ViewportProbe />);
    expect(screen.getByText("sheet-over-bench")).toBeInTheDocument();

    viewport.changeScreen(344, 882);
    expect(screen.getByText("phone")).toBeInTheDocument();
  });

  it("gives phone and does not throw when matchMedia is not a function", () => {
    stubViewport(1920);
    vi.stubGlobal("matchMedia", undefined);
    render(<ViewportProbe />);
    expect(screen.getByText("phone")).toBeInTheDocument();
  });

  it("stops listening on unmount", () => {
    const added = vi.spyOn(window, "addEventListener");
    const removed = vi.spyOn(window, "removeEventListener");
    const viewport = stubViewport(1920);
    const { unmount } = render(<ViewportProbe />);
    expect(viewport.listening()).toBe(2);
    const resizes = added.mock.calls.filter(([type]) => type === "resize");
    expect(resizes).toHaveLength(1);

    unmount();

    expect(viewport.listening()).toBe(0);
    for (const [type, listener] of resizes) {
      expect(removed).toHaveBeenCalledWith(type, listener);
    }
  });
});

describe("useLayout", () => {
  it("gives phone with no provider, whatever the viewport says", () => {
    stubViewport(1920);
    // The viewport really does say columns, so `phone` below can only be the default.
    expect(readLayout()).toBe("columns");
    render(<LayoutProbe />);
    expect(screen.getByText("phone")).toBeInTheDocument();
  });

  it("gives the provided value inside LayoutContext.Provider", () => {
    render(
      <LayoutContext.Provider value="columns">
        <LayoutProbe />
      </LayoutContext.Provider>,
    );
    expect(screen.getByText("columns")).toBeInTheDocument();
  });
});
