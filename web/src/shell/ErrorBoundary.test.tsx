import { render, screen } from "@testing-library/react";
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
    // standalone mode is a white screen with no address bar to reload from.
    expect(document.body.textContent).toContain("gone");
    expect(vi.mocked(console.error).mock.calls.some(([first]) => first === "A bench threw while rendering")).toBe(
      true,
    );
  });

  it("does not un-fail on a re-render: the way back is a fresh mount", () => {
    function Host() {
      const [throwing, setThrowing] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setThrowing(false)}>
            fix it
          </button>
          <ErrorBoundary fallback={<p>gone</p>}>
            <Boom throwing={throwing} />
          </ErrorBoundary>
        </>
      );
    }
    const { rerender, unmount } = render(<Host />);
    expect(screen.getByText("gone")).toBeInTheDocument();
    rerender(<Host />);
    // A boundary that healed on the next render would re-run the same bad
    // state under the same finger. The Workshop's way out is `Layer`
    // unmounting it, which is this:
    expect(screen.getByText("gone")).toBeInTheDocument();
    unmount();
    render(
      <ErrorBoundary fallback={<p>gone</p>}>
        <Boom throwing={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("the bench")).toBeInTheDocument();
  });
});
