import { act, render, screen } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { useOnline } from "./useOnline";

/** What a browser does when the network changes: the flag first, then the event. */
function phoneGoes(state: "online" | "offline"): void {
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(state === "online");
  act(() => void window.dispatchEvent(new Event(state)));
}

function Probe() {
  return <span>{useOnline() ? "online" : "offline"}</span>;
}

describe("useOnline", () => {
  it("starts from what the phone says now", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    render(<Probe />);
    expect(screen.getByText("offline")).toBeInTheDocument();
  });

  it("follows the offline event", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    render(<Probe />);
    phoneGoes("offline");
    expect(screen.getByText("offline")).toBeInTheDocument();
  });

  it("follows the online event", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    render(<Probe />);
    phoneGoes("online");
    expect(screen.getByText("online")).toBeInTheDocument();
  });

  it("does not lose a change that lands before it is listening", () => {
    // The first render comes before any effect, and an event fired between the
    // two reaches no listener — a phone handing over from Wi-Fi to cellular as
    // the app opens. A parent's layout effect runs in exactly that gap.
    function Launch() {
      useLayoutEffect(() => {
        vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
        window.dispatchEvent(new Event("offline"));
      }, []);
      return <Probe />;
    }
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    render(<Launch />);
    expect(screen.getByText("offline")).toBeInTheDocument();
  });

  it("stops listening on unmount", () => {
    const added = vi.spyOn(window, "addEventListener");
    const removed = vi.spyOn(window, "removeEventListener");
    const { unmount } = render(<Probe />);
    const listeners = added.mock.calls.filter(([type]) => type === "online" || type === "offline");
    expect(listeners.map(([type]) => type).sort()).toEqual(["offline", "online"]);

    unmount();

    for (const [type, listener] of listeners) {
      expect(removed).toHaveBeenCalledWith(type, listener);
    }
  });
});
