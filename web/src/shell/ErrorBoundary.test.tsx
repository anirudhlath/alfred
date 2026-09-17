import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom({ throwing }: { throwing: boolean }) {
  if (throwing) throw new Error("bad body");
  return <p>the bench</p>;
}

beforeEach(() => {
  // React prints the caught error itself, and the boundary prints its own.
  // Both are wanted in a browser and neither is wanted in the runner's output.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ErrorBoundary", () => {
  it("draws its children while nothing is wrong", () => {
    render(
      <ErrorBoundary fallback={<p>gone</p>}>
        <Boom throwing={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("the bench")).toBeInTheDocument();
    expect(screen.queryByText("gone")).toBeNull();
  });

  it("holds the fallback in place of a subtree that threw, and says so once", () => {
    render(
      <ErrorBoundary fallback={<p>gone</p>}>
        <Boom throwing />
      </ErrorBoundary>,
    );
    expect(screen.getByText("gone")).toBeInTheDocument();
    // Without a boundary React unmounts the whole tree, which on a phone in
    // standalone mode is a white screen with no address bar to reload from. The
    // fallback is the *whole* of what is left, so the tree is checked for the
    // child's own text rather than for the fallback's, which the line above
    // already has.
    expect(screen.queryByText("the bench")).toBeNull();
    expect(vi.mocked(console.error).mock.calls.some(([first]) => first === "A bench threw while rendering")).toBe(
      true,
    );
  });

  it("does not un-fail on a re-render: the way back is a fresh mount", () => {
    /**
     * `Layer` in miniature. `mounted` is the layer opening and closing — the
     * app's only way out of a failed bench — and it is a *conditional* around
     * the same boundary in the same tree position, not a second `render()`: a
     * fresh `render()` builds a new container and could not carry the old
     * instance's state whatever the boundary did, so it would repeat the first
     * test in this file rather than say anything new.
     */
    function Host() {
      const [throwing, setThrowing] = useState(true);
      const [mounted, setMounted] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setThrowing(false)}>
            fix it
          </button>
          <button type="button" onClick={() => setMounted((open) => !open)}>
            toggle the layer
          </button>
          {mounted && (
            <ErrorBoundary fallback={<p>gone</p>}>
              <Boom throwing={throwing} />
            </ErrorBoundary>
          )}
        </>
      );
    }
    render(<Host />);
    expect(screen.getByText("gone")).toBeInTheDocument();
    // The child is *fixed* before the re-render, which is the only version of
    // this that proves anything: re-rendering a child that still throws draws
    // the fallback down both branches, so a boundary that healed on update
    // would pass it. Once `throwing` is false the two branches disagree —
    // healed shows `the bench`, ours keeps `gone`.
    fireEvent.click(screen.getByRole("button", { name: "fix it" }));
    // A boundary that healed on the next render would re-run the same bad
    // state under the same finger. The Workshop's way out is `Layer`
    // unmounting it, which is the two clicks below.
    expect(screen.getByText("gone")).toBeInTheDocument();
    expect(screen.queryByText("the bench")).toBeNull();

    const layer = screen.getByRole("button", { name: "toggle the layer" });
    fireEvent.click(layer);
    expect(screen.queryByText("gone")).toBeNull();
    fireEvent.click(layer);
    // Closing and reopening is the whole reset — the surface's own lifecycle
    // rather than a second mechanism. It works only while `failed` lives on the
    // instance: anything remembered outside it would survive this and hand the
    // reader a bench that is permanently broken.
    expect(screen.getByText("the bench")).toBeInTheDocument();
  });
});
