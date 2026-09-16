import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { humaniseTool } from "@/lib/format";
import type { Routine } from "@/lib/memory";
import { routine } from "@/test/fixtures";
import { RoutineRow } from "./RoutineRow";

/** One row in the list it lives in, as `EventRow.test.tsx` renders its own. */
function renderRow(overrides: Partial<Routine> = {}, open = false) {
  const onToggle = vi.fn();
  const { rerender } = render(
    <ul>
      <RoutineRow routine={routine(overrides)} open={open} onToggle={onToggle} />
    </ul>,
  );
  return {
    onToggle,
    reopen: (next: boolean) =>
      rerender(
        <ul>
          <RoutineRow routine={routine(overrides)} open={next} onToggle={onToggle} />
        </ul>,
      ),
  };
}

const bars = () => Array.from(screen.getByRole("img").children) as HTMLElement[];

describe("RoutineRow", () => {
  it("names the routine and reports the confidence it is worth now", () => {
    renderRow();
    expect(screen.getByText("evening-lights")).toBeInTheDocument();
    expect(screen.getByText("0.82 · +0.11 since last week")).toBeInTheDocument();
  });

  it("colours a rising routine green", () => {
    renderRow();
    expect(screen.getByText("0.82 · +0.11 since last week").style.color).toBe("var(--green-text)");
  });

  // A routine losing confidence is the Librarian working, not a fault: --fg2,
  // and no red anywhere on the bench.
  it("colours a falling routine no differently from any other meta", () => {
    renderRow({ confidence_history: [0.9, 0.82] });
    expect(screen.getByText("0.82 · -0.08 since last week").style.color).toBe("var(--fg2)");
  });

  it("fills the rail up to the current stage and leaves the ones ahead hollow", () => {
    renderRow({ state: "dormant" });
    const rail = screen.getByTestId("lifecycle-rail");
    // A picture of the lifecycle: the words below carry the same fact.
    expect(rail).toHaveAttribute("aria-hidden", "true");
    const dots = within(rail).getAllByTestId("stage-dot");
    expect(dots).toHaveLength(4);
    expect(dots.map((dot) => dot.style.background)).toEqual([
      "var(--line)",
      "var(--line)",
      "var(--accent)",
      "transparent",
    ]);
    expect(dots[3].style.borderColor).toBe("var(--line)");
  });

  it("says which stage the routine is at in words", () => {
    renderRow({ state: "archived" });
    expect(screen.getByText("stage: archived")).toHaveClass("sr-only");
  });

  it("toggles by name, and says whether it is open", () => {
    const { onToggle, reopen } = renderRow();
    const row = screen.getByRole("button", { expanded: false });
    fireEvent.click(row);
    expect(onToggle).toHaveBeenCalledWith("evening-lights");
    reopen(true);
    expect(screen.getByRole("button", { expanded: true })).toBeInTheDocument();
  });

  it("keeps the steps and the detail folded away until it is opened", () => {
    renderRow();
    expect(screen.queryByText("2 steps · learned from 2 memories")).toBeNull();
    expect(screen.queryByText("Dim the living room to 30%")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("lists the steps in order with the tool each one runs", () => {
    renderRow({}, true);
    expect(screen.getByText("2 steps · learned from 2 memories")).toBeInTheDocument();
    const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(steps).toHaveLength(2);
    expect(steps[0]).toHaveTextContent("Dim the living room to 30%");
    expect(within(steps[0]).getByText(humaniseTool("home.light_set"))).toBeInTheDocument();
  });

  it("renders a step with no action as its description alone", () => {
    renderRow({ steps: [{ description: "Wait for the TV to start", action: null }] }, true);
    const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(steps).toHaveLength(1);
    expect(steps[0]).toHaveTextContent("Wait for the TV to start");
    expect(steps[0].textContent).toBe("Wait for the TV to start");
  });

  it("draws one bar per reading and says what the picture means", () => {
    renderRow({}, true);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "confidence over the last 3 consolidations, now 0.82",
    );
    expect(bars()).toHaveLength(3);
  });

  // A flat line is a claim about a routine nothing has scored yet.
  it("draws no sparkline at all for a routine with no history", () => {
    renderRow({ confidence_history: [] }, true);
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("draws only the last eight readings of a longer history", () => {
    const history = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.78, 0.79, 0.81, 0.82];
    renderRow({ confidence_history: history }, true);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "confidence over the last 8 consolidations, now 0.82",
    );
    const drawn = bars();
    expect(drawn).toHaveLength(8);
    // The tallest of the eight is the newest, which is also the only solid one.
    expect(drawn.map((bar) => bar.style.opacity)).toEqual([
      "0.35",
      "0.35",
      "0.35",
      "0.35",
      "0.35",
      "0.35",
      "0.35",
      "1",
    ]);
    expect(drawn.every((bar) => bar.style.background === "var(--accent)")).toBe(true);
  });

  it("is a 44 px tap target", () => {
    renderRow();
    expect(screen.getByRole("button")).toHaveClass("min-h-11");
  });
});
