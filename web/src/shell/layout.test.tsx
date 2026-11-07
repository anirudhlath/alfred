import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LayoutContext, readLayout, useLayout, useViewportLayout } from "./layout";

const QUERIES: Record<string, (width: number, height: number) => boolean> = {
  "(min-width: 760px) and (min-height: 500px)": (width, height) => width >= 760 && height >= 500,
  "(min-width: 1100px) and (min-height: 500px)": (width, height) => width >= 1100 && height >= 500,
};

/**
 * A viewport of this size behind `matchMedia`. Like a browser, `resize` fires
 * `change` only on a query whose answer flipped, so a hook listening to just one
 * of the two misses the other's crossing. Any other query throws: the two strings
 * are the contract.
 */
function stubViewport(startWidth: number, startHeight: number) {
  let width = startWidth;
  let height = startHeight;
  const known = Object.keys(QUERIES);
  const listeners = new Map(known.map((query) => [query, new Set<() => void>()]));

  function answer(query: string): boolean {
    const evaluate = QUERIES[query];
    if (!evaluate) throw new Error(`unexpected media query: ${query}`);
    return evaluate(width, height);
  }

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
    resize(nextWidth: number, nextHeight: number) {
      const before = known.map(answer);
      width = nextWidth;
      height = nextHeight;
      const flipped = known.filter((query, index) => answer(query) !== before[index]);
      act(() => {
        for (const query of flipped) listeners.get(query)?.forEach((listener) => listener());
      });
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
    [393, 852, "phone"],
    [759, 900, "phone"],
    [852, 393, "phone"],
    [760, 900, "sheet-over-bench"],
    [1099, 900, "sheet-over-bench"],
    [1100, 900, "columns"],
    [1920, 1080, "columns"],
  ])("at %i×%i gives %s", (width, height, layout) => {
    stubViewport(width, height);
    expect(readLayout()).toBe(layout);
  });

  it("gives phone when matchMedia is not a function", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(readLayout()).toBe("phone");
  });
});

describe("useViewportLayout", () => {
  it("re-renders when the viewport crosses 760", () => {
    const viewport = stubViewport(759, 900);
    render(<ViewportProbe />);
    expect(screen.getByText("phone")).toBeInTheDocument();

    viewport.resize(760, 900);
    expect(screen.getByText("sheet-over-bench")).toBeInTheDocument();

    viewport.resize(759, 900);
    expect(screen.getByText("phone")).toBeInTheDocument();
  });

  it("re-renders when the viewport crosses 1100", () => {
    const viewport = stubViewport(1099, 900);
    render(<ViewportProbe />);
    expect(screen.getByText("sheet-over-bench")).toBeInTheDocument();

    viewport.resize(1100, 900);
    expect(screen.getByText("columns")).toBeInTheDocument();

    viewport.resize(1099, 900);
    expect(screen.getByText("sheet-over-bench")).toBeInTheDocument();
  });

  it("gives phone and does not throw when matchMedia is not a function", () => {
    vi.stubGlobal("matchMedia", undefined);
    render(<ViewportProbe />);
    expect(screen.getByText("phone")).toBeInTheDocument();
  });

  it("stops listening on unmount", () => {
    const viewport = stubViewport(1920, 1080);
    const { unmount } = render(<ViewportProbe />);
    expect(viewport.listening()).toBe(2);

    unmount();

    expect(viewport.listening()).toBe(0);
  });
});

describe("useLayout", () => {
  it("gives phone with no provider, whatever the viewport says", () => {
    stubViewport(1920, 1080);
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
