import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Layer } from "./Layer";
import { Sheet } from "./Sheet";

/** Only the reduce-motion query answers `matches`; everything else stays false. */
function stubReducedMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: matches && query === "(prefers-reduced-motion: reduce)",
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

/** The app column a real page has, so the surfaces have something to make inert. */
function mountApp(): HTMLButtonElement {
  const root = document.createElement("div");
  root.id = "root";
  const button = document.createElement("button");
  button.textContent = "Talk";
  root.append(button);
  document.body.append(root);
  return button;
}

beforeEach(() => {
  vi.useFakeTimers();
  stubReducedMotion(false);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.getElementById("root")?.remove();
});

describe("Layer", () => {
  it("renders nothing while closed", () => {
    render(
      <Layer open={false} label="Door">
        <p>hidden</p>
      </Layer>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("rises as a labelled modal with the caller's duration", () => {
    render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    const dialog = screen.getByRole("dialog", { name: "Door" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog.className).toContain("rise-in");
    expect(dialog.className).toContain("z-30");
    expect(dialog.style.getPropertyValue("--layer-duration")).toBe("420ms");
    expect(screen.getByText("visible")).toBeInTheDocument();
  });

  it("stacks gates above layers", () => {
    render(
      <Layer open label="Session lapsed" level="gate">
        <p>gate</p>
      </Layer>,
    );
    expect(screen.getByRole("dialog").className).toContain("z-40");
  });

  it("stays mounted for the leave animation, then unmounts", () => {
    const { rerender } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    expect(screen.getByRole("dialog").className).toContain("rise-out");
    act(() => vi.advanceTimersByTime(419));
    expect(screen.queryByRole("dialog")).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("leaves in 200 ms under reduce-motion", () => {
    stubReducedMotion(true);
    const { rerender } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    act(() => vi.advanceTimersByTime(200));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("re-opening mid-leave cancels the unmount", () => {
    const { rerender } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    act(() => vi.advanceTimersByTime(200));
    rerender(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    act(() => vi.advanceTimersByTime(1000));
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("rise-in");
    expect(dialog.className).not.toContain("rise-out");
  });

  it("clears the leave timer when unmounted mid-leave", () => {
    const { rerender, unmount } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    // React keeps a scheduler timer of its own; count the leave timer on top of it.
    const idle = vi.getTimerCount();
    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    expect(vi.getTimerCount()).toBe(idle + 1);
    unmount();
    expect(vi.getTimerCount()).toBe(idle);
  });

  it("takes focus, makes the app inert, and gives both back when it has left", () => {
    const talk = mountApp();
    talk.focus();
    const { rerender } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    expect(document.activeElement).toBe(screen.getByRole("dialog"));
    expect(document.getElementById("root")).toHaveAttribute("inert");

    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    // Still inert while it sinks: nothing behind it is reachable mid-leave.
    expect(document.getElementById("root")).toHaveAttribute("inert");
    act(() => vi.advanceTimersByTime(420));
    expect(document.getElementById("root")).not.toHaveAttribute("inert");
    expect(document.activeElement).toBe(talk);
  });

  it("keeps the app inert until the last surface has left", () => {
    mountApp();
    const { rerender } = render(
      <>
        <Sheet open title="Held back" onClose={() => {}}>
          <p>sheet</p>
        </Sheet>
        <Layer open label="Door" durationMs={420}>
          <p>door</p>
        </Layer>
      </>,
    );
    rerender(
      <>
        <Sheet open title="Held back" onClose={() => {}}>
          <p>sheet</p>
        </Sheet>
        <Layer open={false} label="Door" durationMs={420}>
          <p>door</p>
        </Layer>
      </>,
    );
    act(() => vi.advanceTimersByTime(420));
    expect(screen.queryByRole("dialog", { name: "Door" })).toBeNull();
    expect(document.getElementById("root")).toHaveAttribute("inert");
  });

  it("a gate over a sheet makes the sheet inert, swallows Escape, and keeps focus", () => {
    mountApp().focus();
    const onClose = vi.fn();
    const stacked = (sheetOpen: boolean) => (
      <>
        <Sheet open={sheetOpen} title="Held back" onClose={onClose}>
          <p>sheet</p>
        </Sheet>
        <Layer open label="Session lapsed" level="gate">
          <button type="button">Sign in</button>
        </Layer>
      </>
    );
    const { rerender } = render(stacked(true));
    const gate = screen.getByRole("dialog", { name: "Session lapsed" });
    expect(document.activeElement).toBe(gate);
    expect(screen.getByRole("dialog", { name: "Held back" })).toHaveAttribute("inert");

    fireEvent.keyDown(gate, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();

    // The sheet leaves underneath: focus stays in the gate, the app stays inert.
    rerender(stacked(false));
    act(() => vi.advanceTimersByTime(380));
    expect(screen.queryByRole("dialog", { name: "Held back" })).toBeNull();
    expect(document.activeElement).toBe(gate);
    expect(document.getElementById("root")).toHaveAttribute("inert");
  });

  it("gives the sheet back when the gate over it leaves", () => {
    mountApp();
    const stacked = (gateOpen: boolean) => (
      <>
        <Sheet open title="Held back" onClose={() => {}}>
          <p>sheet</p>
        </Sheet>
        <Layer open={gateOpen} label="Session lapsed" level="gate">
          <button type="button">Sign in</button>
        </Layer>
      </>
    );
    const { rerender } = render(stacked(true));
    const sheet = screen.getByRole("dialog", { name: "Held back" });
    rerender(stacked(false));
    // Still inert while the gate sinks.
    expect(sheet).toHaveAttribute("inert");
    act(() => vi.advanceTimersByTime(400));
    expect(sheet).not.toHaveAttribute("inert");
    expect(document.activeElement).toBe(sheet);
    expect(document.getElementById("root")).toHaveAttribute("inert");
  });
});

describe("Sheet", () => {
  it("shows the title, the children and a Done button", () => {
    render(
      <Sheet open title="Held back" onClose={() => {}}>
        <p>three things</p>
      </Sheet>,
    );
    const dialog = screen.getByRole("dialog", { name: "Held back" });
    expect(dialog.className).toContain("z-20");
    expect(screen.getByText("three things")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("closes on Done, on the scrim and on Escape from inside it", () => {
    const onClose = vi.fn();
    render(
      <Sheet open title="Held back" onClose={onClose}>
        <p>three things</p>
      </Sheet>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.keyDown(screen.getByRole("button", { name: "Done" }), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("unmounts 380 ms after closing", () => {
    const { rerender } = render(
      <Sheet open title="Held back" onClose={() => {}}>
        <p>three things</p>
      </Sheet>,
    );
    rerender(
      <Sheet open={false} title="Held back" onClose={() => {}}>
        <p>three things</p>
      </Sheet>,
    );
    act(() => vi.advanceTimersByTime(379));
    expect(screen.queryByRole("dialog")).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
