import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as sw from "@/lib/sw";
import { useUpdateReady } from "./useUpdateReady";

function Probe() {
  return <span>{useUpdateReady() ? "ready" : "current"}</span>;
}

describe("useUpdateReady", () => {
  it("starts from whatever onUpdateReady says first", () => {
    vi.spyOn(sw, "onUpdateReady").mockImplementation((fn) => {
      fn(true);
      return () => {};
    });
    render(<Probe />);
    expect(screen.getByText("ready")).toBeInTheDocument();
  });

  it("follows every answer after that", () => {
    let announce: (ready: boolean) => void = () => {};
    vi.spyOn(sw, "onUpdateReady").mockImplementation((fn) => {
      announce = fn;
      fn(false);
      return () => {};
    });
    render(<Probe />);
    expect(screen.getByText("current")).toBeInTheDocument();

    // A changed sw.js finishes installing while the app is open…
    act(() => announce(true));
    expect(screen.getByText("ready")).toBeInTheDocument();
    // …and stops waiting: taken over, or made redundant by a newer one.
    act(() => announce(false));
    expect(screen.getByText("current")).toBeInTheDocument();
  });

  it("says no on its first render, and the answer after it", () => {
    // Even with a worker already waiting. The note's live region is then in
    // the page, empty, before its words arrive — the order OfflineNote keeps
    // for the same reason.
    const seen: boolean[] = [];
    function Recorder() {
      seen.push(useUpdateReady());
      return null;
    }
    vi.spyOn(sw, "onUpdateReady").mockImplementation((fn) => {
      fn(true);
      return () => {};
    });
    render(<Recorder />);
    expect(seen[0]).toBe(false);
    expect(seen.at(-1)).toBe(true);
  });

  it("subscribes once, however often it renders", () => {
    // The Room re-renders on every chat frame and every second of a Door's fuse.
    const subscribe = vi.spyOn(sw, "onUpdateReady").mockReturnValue(() => {});
    const { rerender } = render(<Probe />);
    rerender(<Probe />);
    rerender(<Probe />);
    expect(subscribe).toHaveBeenCalledTimes(1);
  });

  it("unsubscribes on unmount", () => {
    const stop = vi.fn();
    vi.spyOn(sw, "onUpdateReady").mockReturnValue(stop);
    const { unmount } = render(<Probe />);
    unmount();
    expect(stop).toHaveBeenCalledTimes(1);
  });
});
