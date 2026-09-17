import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkshopHandle } from "./WorkshopHandle";

describe("WorkshopHandle", () => {
  it("reads workshop under a chevron", () => {
    render(<WorkshopHandle onOpen={() => {}} />);
    const handle = screen.getByRole("button", { name: "Open the Workshop" });
    expect(handle).toHaveTextContent("workshop");
    expect(handle).toHaveClass("min-h-11");
    // The chevron is a picture; the word under it is what is read.
    expect(handle.querySelector("[data-testid=handle-chevron]")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  });

  it("draws no home indicator of its own", () => {
    // The handoff's 139x5 bar is the iPhone frame the mock was drawn on, not the
    // design. iOS draws the real one over env(safe-area-inset-bottom).
    render(<WorkshopHandle onOpen={() => {}} />);
    const handle = screen.getByRole("button", { name: "Open the Workshop" });
    expect(handle.querySelector("[data-testid=handle-bar]")).toBeNull();
  });

  it("opens on a tap", () => {
    const onOpen = vi.fn();
    render(<WorkshopHandle onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: "Open the Workshop" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
