import { render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { Desk, useDeskSlots, type DeskSlots } from "./Desk";
import { LayoutContext, type Layout } from "./layout";

function desk(layout: Layout, detailOpen = false, bench = <p data-testid="bench" />) {
  return (
    <LayoutContext.Provider value={layout}>
      <Desk room={<main data-testid="room" />} bench={bench} detailOpen={detailOpen} />
    </LayoutContext.Provider>
  );
}

/** The root of `Desk`'s tree, the one element carrying the layout's attributes. */
const root = () => screen.getByTestId("room").parentElement as HTMLElement;
/** What `inert` goes on: the bench's own wrapper, which is not the slot. */
const benchBody = () => screen.getByTestId("bench").parentElement as HTMLElement;

describe("Desk", () => {
  it("keeps the Room's DOM node across every layout", () => {
    const { rerender } = render(desk("phone"));
    const room = screen.getByTestId("room");

    rerender(desk("sheet-over-bench"));
    expect(screen.getByTestId("room")).toBe(room);
    expect(root()).toHaveAttribute("data-layout", "sheet-over-bench");

    rerender(desk("columns"));
    expect(screen.getByTestId("room")).toBe(room);
    expect(root()).toHaveAttribute("data-layout", "columns");

    rerender(desk("phone"));
    expect(screen.getByTestId("room")).toBe(room);
    expect(root()).toHaveAttribute("data-layout", "phone");
  });

  it("sets data-detail only in columns with a detail open", () => {
    const { rerender } = render(desk("columns", true));
    expect(root()).toHaveAttribute("data-detail");

    // R2: closed, the grid has no third column to show.
    rerender(desk("columns", false));
    expect(root()).not.toHaveAttribute("data-detail");

    // A detail is a sheet over the bench here, and a sheet on the phone: neither
    // has a column for it.
    rerender(desk("sheet-over-bench", true));
    expect(root()).not.toHaveAttribute("data-detail");
    rerender(desk("phone", true));
    expect(root()).not.toHaveAttribute("data-detail");
  });

  it("makes the bench body inert only over the bench", () => {
    const { rerender } = render(desk("sheet-over-bench", true));
    expect(benchBody()).toHaveAttribute("inert");
    // The slot holds the sheet, so going inert there would take the sheet with it.
    expect(benchBody().parentElement).not.toHaveAttribute("inert");

    rerender(desk("sheet-over-bench", false));
    expect(benchBody()).not.toHaveAttribute("inert");

    // Beside the bench rather than over it: the reader's place stays live.
    rerender(desk("columns", true));
    expect(benchBody()).not.toHaveAttribute("inert");
    expect(benchBody().parentElement).not.toHaveAttribute("inert");

    rerender(desk("phone", true));
    expect(benchBody()).not.toHaveAttribute("inert");
  });

  it("hands out both slots", () => {
    const seen = vi.fn<(slots: DeskSlots) => void>();
    function Consumer() {
      const slots = useDeskSlots();
      useEffect(() => seen(slots), [slots]);
      return <p data-testid="bench" />;
    }

    render(desk("columns", true, <Consumer />));

    const { overBench, detail } = seen.mock.lastCall?.[0] ?? { overBench: null, detail: null };
    expect(overBench).toBeInstanceOf(HTMLElement);
    expect(overBench?.isConnected).toBe(true);
    expect(detail).toBeInstanceOf(HTMLElement);
    expect(detail?.isConnected).toBe(true);
    // Each is the slot its name says: the one that holds the bench, and another.
    expect(overBench).toContainElement(screen.getByTestId("bench"));
    expect(detail).not.toBe(overBench);
    expect(detail).not.toContainElement(screen.getByTestId("bench"));
  });
});
