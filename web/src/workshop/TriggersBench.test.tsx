import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { trigger } from "@/test/fixtures";
import { TriggersBench } from "./TriggersBench";
import type { Triggers } from "./useTriggers";

/** Three stored triggers, one under each of three different chips. */
const bins = trigger();
const sweep = trigger({
  trigger_id: "trg_sweep",
  name: "Evening sweep",
  conditions: { cron: "0 19 * * 4", run_at: null },
});
const door = trigger({
  trigger_id: "trg_door",
  name: "Front door",
  trigger_type: "sensor",
  conditions: { entity_id: "binary_sensor.front_door", state_match: "on" },
});

/**
 * The bench is a pure view over one state object, so its tests build that
 * object rather than a `QueryClient` — the reason `MemoryBench.test.tsx` and
 * `ActivityBench.test.tsx` need no providers either.
 */
function state(overrides: Partial<Triggers> = {}): Triggers {
  const triggers = overrides.triggers ?? [bins, sweep, door];
  return {
    kind: "all",
    setKind: vi.fn(),
    triggers,
    shown: triggers,
    open: null,
    toggleOpen: vi.fn(),
    pending: {},
    toggle: vi.fn(),
    fire: vi.fn(),
    fired: {},
    loading: false,
    error: null,
    ...overrides,
  };
}

const chips = () => within(screen.getByRole("group", { name: "Trigger kinds" })).getAllByRole("button");

const FOOTER_ONE =
  "Switches are fire-and-forget: the server queues the change and the scheduler picks it up within 60 s. A row keeps its old state, with a note, until a fresh read confirms.";
const FOOTER_TWO = "Nothing here edits a trigger — ask Alfred to change or remove one.";

describe("TriggersBench", () => {
  it("offers the five kinds, and counts only the one that means all of them", () => {
    render(<TriggersBench triggers={state()} />);
    expect(chips().map((chip) => chip.textContent)).toEqual([
      "All 3",
      "Time",
      "Schedule",
      "Sensor",
      "Composite",
    ]);
  });

  // Filters, not tabs: a group of toggles the reader walks with Tab, which is
  // why these do not take `tabs.ts`'s roving arrow keys.
  it("marks the chosen kind and hands a tap back to the hook", () => {
    const triggers = state({ kind: "sensor", shown: [door] });
    render(<TriggersBench triggers={triggers} />);
    expect(chips().map((chip) => chip.getAttribute("aria-pressed"))).toEqual([
      "false",
      "false",
      "false",
      "true",
      "false",
    ]);
    fireEvent.click(chips()[2]);
    expect(triggers.setKind).toHaveBeenCalledWith("schedule");
  });

  it("gives each trigger the chip lets through a row of its own", () => {
    render(<TriggersBench triggers={state({ kind: "schedule", shown: [sweep] })} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("Evening sweep")).toBeInTheDocument();
    expect(screen.queryByText("Bins out")).toBeNull();
  });

  it("says the house has no triggers when it really has none", () => {
    render(<TriggersBench triggers={state({ triggers: [], shown: [] })} />);
    expect(screen.getByText("No triggers yet.")).toBeInTheDocument();
  });

  // A different fact from the one above, and the reader cannot see the chip
  // they set three taps ago from the empty list alone.
  it("names the filter that emptied the list, when the list is not empty", () => {
    render(<TriggersBench triggers={state({ kind: "composite", shown: [] })} />);
    expect(screen.getByText("No composite triggers.")).toBeInTheDocument();
    expect(screen.queryByText("No triggers yet.")).toBeNull();
  });

  it("says in the footer that a switch is a request, and that nothing here edits", () => {
    render(<TriggersBench triggers={state()} />);
    expect(screen.getByText(FOOTER_ONE)).toHaveClass("t-meta-strong");
    // There is no create/edit/delete route; a reader who does not know that
    // will hunt for a button that is not there.
    expect(screen.getByText(FOOTER_TWO)).toHaveClass("t-meta-strong");
  });

  it("reports a failed read without taking the last-known rows away", () => {
    render(<TriggersBench triggers={state({ error: "Session store unavailable" })} />);
    expect(screen.getByText("Session store unavailable")).toHaveClass("t-meta-strong");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("wires each row's controls to the hook that owns them", () => {
    const triggers = state();
    render(<TriggersBench triggers={triggers} />);
    fireEvent.click(screen.getAllByRole("switch")[2]);
    expect(triggers.toggle).toHaveBeenCalledWith(door);
    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    expect(triggers.toggleOpen).toHaveBeenCalledWith("trg_bins");
  });

  it("opens the one row the hook names, and passes it what it has queued", () => {
    const triggers = state({
      open: "trg_door",
      pending: { trg_door: { kind: "disabling", at: new Date(2026, 8, 16, 21, 15, 0).getTime() } },
    });
    render(<TriggersBench triggers={triggers} />);
    expect(screen.getAllByRole("button", { expanded: true })).toHaveLength(1);
    expect(
      screen.getByText("queued 21:15 · disabling · takes effect within 60 s"),
    ).toBeInTheDocument();
  });

  it("gives every chip a tap target of at least 44 px", () => {
    render(<TriggersBench triggers={state()} />);
    // 32 px of chip, 6 px of hit area above and below it.
    for (const chip of chips()) {
      expect(chip).toHaveClass("h-8");
      expect(chip).toHaveClass("after:-inset-y-1.5");
    }
  });
});
