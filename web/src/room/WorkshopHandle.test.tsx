import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkshopHandle } from "./WorkshopHandle";

describe("WorkshopHandle", () => {
  it("reads workshop under a chevron, over the bar", () => {
    render(<WorkshopHandle onOpen={() => {}} />);
    const handle = screen.getByRole("button", { name: "Open the Workshop" });
    expect(handle).toHaveTextContent("workshop");
    expect(handle).toHaveClass("min-h-11");
    expect(handle.querySelector("[data-testid=handle-bar]")).toHaveStyle({ background: "var(--fg)" });
  });

  it("opens on a tap", () => {
    const onOpen = vi.fn();
    render(<WorkshopHandle onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: "Open the Workshop" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
