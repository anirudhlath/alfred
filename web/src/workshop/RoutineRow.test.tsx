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
const heights = () => bars().map((bar) => bar.style.height);

describe("RoutineRow", () => {
  it("names the routine and reports the confidence it is worth now", () => {
    renderRow();
    expect(screen.getByText("evening-lights")).toBeInTheDocument();
    expect(screen.getByText("0.82 · +0.11 since last week")).toBeInTheDocument();
  });

  it("gives a rising routine the accent", () => {
    renderRow();
    expect(screen.getByText("0.82 · +0.11 since last week").style.color).toBe("var(--accent-text)");
  });

  // A routine losing confidence is the Librarian working, not a fault: it takes
  // `.t-meta-strong`'s own --fg2 and no more, and no red anywhere on the bench.
  it("colours a falling routine no differently from any other meta", () => {
    renderRow({ confidence_history: [0.9, 0.82] });
    const trend = screen.getByText("0.82 · -0.08 since last week");
    expect(trend.style.color).toBe("");
    expect(trend).toHaveClass("t-meta-strong");
  });

  it("fills the rail up to the stage the routine is at", () => {
    renderRow({ state: "dormant" });
    const rail = screen.getByTestId("lifecycle-rail");
    expect(within(rail).getAllByTestId("stage-bar").map((bar) => bar.style.background)).toEqual([
      "var(--muted)",
      "var(--muted)",
      "var(--accent-text)",
      "var(--line)",
    ]);
  });

  it("names all four stages and marks which one is now", () => {
    renderRow({ state: "archived" });
    const rail = screen.getByTestId("lifecycle-rail");
    expect(within(rail).getAllByTestId("stage-column").map((column) => column.textContent)).toEqual([
      "candidate",
      "active",
      "dormant",
      "archived",
    ]);
    const current = within(rail)
      .getAllByTestId("stage-column")
      .map((column) => column.getAttribute("aria-current"));
    expect(current).toEqual([null, null, null, "step"]);
  });

  // The handoff mutes a routine that has fallen out of use; --fg2 rather than
  // --muted, which is 3.46:1 on --bg in light (index.css, .t-meta-strong).
  it("steps a dormant routine's name back, and leaves an active one alone", () => {
    renderRow({ state: "dormant" });
    expect(screen.getByText("evening-lights").style.color).toBe("var(--fg2)");
    renderRow({ state: "active" });
    expect(screen.getAllByText("evening-lights")[1].style.color).toBe("");
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

  it("numbers the steps in order and names the tool each one runs", () => {
    renderRow({}, true);
    expect(screen.getByText("2 steps · learned from 2 memories")).toBeInTheDocument();
    const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(steps).toHaveLength(2);
    expect(steps[0]).toHaveTextContent("1.");
    expect(steps[1]).toHaveTextContent("2.");
    expect(steps[0]).toHaveTextContent("Dim the living room to 30%");
    expect(within(steps[0]).getByText(humaniseTool("home.light_set"))).toBeInTheDocument();
  });

  it("renders a step with no action as its description alone", () => {
    renderRow({ steps: [{ description: "Wait for the TV to start", action: null }] }, true);
    const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(steps).toHaveLength(1);
    // The ordinal and the description, and nothing where a tool would be.
    expect(steps[0].children).toHaveLength(2);
    expect(steps[0].textContent).toBe("1.Wait for the TV to start");
  });

  it("draws one bar per reading and captions what the picture is", () => {
    renderRow({}, true);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "confidence over the last 3 consolidations, now 0.82",
    );
    expect(bars()).toHaveLength(3);
    // Counted, not the handoff's flat "last 8": three readings are three.
    const caption = screen.getByText("confidence, last 3 consolidations");
    expect(caption).toHaveAttribute("aria-hidden", "true");
  });

  // A flat line is a claim about a routine nothing has scored yet.
  it("draws no sparkline at all for a routine with no history", () => {
    renderRow({ confidence_history: [] }, true);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByText(/consolidations$/)).toBeNull();
  });

  it("draws only the last eight readings, with the newest tallest and in the accent", () => {
    const history = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.78, 0.79, 0.81, 0.9];
    renderRow({ confidence_history: history }, true);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "confidence over the last 8 consolidations, now 0.82",
    );
    const drawn = bars();
    expect(drawn).toHaveLength(8);
    // The last eight of the twelve, 28 px at full confidence: 0.5 → 14 px,
    // 0.9 → 25 px, and the two readings a hundredth apart round to the same bar.
    expect(heights()).toEqual(["14px", "17px", "20px", "21px", "22px", "22px", "23px", "25px"]);
    // Dimmed by token, never by an opacity: a whole-bar alpha composites what
    // is under it and is invisible to `src/test/contrast.ts`. --accent-text for
    // the newest and --muted for the rest, both ≥3:1 on --bg in both themes
    // (pinned in `test/contrast.test.ts`); raw --accent is 2.34:1 in light and
    // at 0.7 alpha the older bars were 1.67:1.
    expect(drawn.map((bar) => bar.style.opacity)).toEqual(Array(8).fill(""));
    expect(drawn.map((bar) => bar.style.background)).toEqual([
      ...Array(7).fill("var(--muted)"),
      "var(--accent-text)",
    ]);
  });

  // A consolidation that scored zero still happened, and a score outside 0-1 is
  // a bug in the store rather than a bar taller than its own box.
  it("floors a zero reading and clamps one outside the scale", () => {
    renderRow({ confidence_history: [0, -1, 2, 0.5] }, true);
    expect(heights()).toEqual(["2px", "2px", "28px", "14px"]);
  });

  it("is a 44 px tap target", () => {
    renderRow();
    expect(screen.getByRole("button")).toHaveClass("min-h-11");
  });
});
