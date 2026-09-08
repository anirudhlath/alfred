import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Layer } from "./Layer";
import { Sheet } from "./Sheet";

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

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Layer", () => {
  it("renders nothing while closed", () => {
    render(
      <Layer open={false} label="The Door">
        <p>inside</p>
      </Layer>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders a labelled modal that rises for its own duration", () => {
    render(
      <Layer open label="The Door" durationMs={420}>
        <p>inside</p>
      </Layer>,
    );
    const dialog = screen.getByRole("dialog", { name: "The Door" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveClass("rise-in");
    expect(dialog.style.getPropertyValue("--layer-duration")).toBe("420ms");
    expect(screen.getByText("inside")).toBeInTheDocument();
  });

  it("stays mounted for the whole leave animation, then unmounts", () => {
    const { rerender } = render(
      <Layer open label="The Door" durationMs={420}>
        <p>inside</p>
      </Layer>,
    );

    rerender(
      <Layer open={false} label="The Door" durationMs={420}>
        <p>inside</p>
      </Layer>,
    );
    expect(screen.getByRole("dialog")).toHaveClass("rise-out");

    act(() => void vi.advanceTimersByTime(419));
    expect(screen.queryByRole("dialog")).not.toBeNull();

    act(() => void vi.advanceTimersByTime(1));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("leaves in 200 ms under reduce-motion, whatever the duration says", () => {
    stubReducedMotion(true);
    const { rerender } = render(
      <Layer open label="The Door" durationMs={420}>
        <p>inside</p>
      </Layer>,
    );

    rerender(
      <Layer open={false} label="The Door" durationMs={420}>
        <p>inside</p>
      </Layer>,
    );
    act(() => void vi.advanceTimersByTime(200));

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cancels the unmount when it is re-opened mid-leave", () => {
    const { rerender } = render(
      <Layer open label="The Door">
        <p>inside</p>
      </Layer>,
    );

    rerender(
      <Layer open={false} label="The Door">
        <p>inside</p>
      </Layer>,
    );
    act(() => void vi.advanceTimersByTime(100));
    rerender(
      <Layer open label="The Door">
        <p>inside</p>
      </Layer>,
    );
    act(() => void vi.advanceTimersByTime(1000));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveClass("rise-in");
  });
});

describe("Sheet", () => {
  it("renders its title, its children and an explicit Done", () => {
    render(
      <Sheet open title="Held back" onClose={() => {}}>
        <p>two waiting</p>
      </Sheet>,
    );
    expect(screen.getByRole("dialog", { name: "Held back" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Held back" })).toBeInTheDocument();
    expect(screen.getByText("two waiting")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("closes on Done and on the scrim", () => {
    const onClose = vi.fn();
    render(
      <Sheet open title="Held back" onClose={onClose}>
        <p>two waiting</p>
      </Sheet>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("unmounts 380 ms after it is closed", () => {
    const { rerender } = render(
      <Sheet open title="Held back" onClose={() => {}}>
        <p>two waiting</p>
      </Sheet>,
    );

    rerender(
      <Sheet open={false} title="Held back" onClose={() => {}}>
        <p>two waiting</p>
      </Sheet>,
    );
    act(() => void vi.advanceTimersByTime(379));
    expect(screen.queryByRole("dialog")).not.toBeNull();

    act(() => void vi.advanceTimersByTime(1));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
