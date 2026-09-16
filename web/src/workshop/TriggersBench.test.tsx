import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TRIGGER_NOW, trigger } from "@/test/fixtures";
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
    read: true,
    loading: false,
    error: null,
    ...overrides,
  };
}

const chips = () =>
  within(screen.getByRole("group", { name: "Trigger kinds" })).getAllByRole("button");

const FOOTER_ONE =
  "Switches are fire-and-forget: the server queues the change and the scheduler " +
  "picks it up within 60 s. A row keeps its old state, with a note, until a fresh read confirms.";
const FOOTER_TWO = "Nothing here edits a trigger — ask Alfred to change or remove one.";

/**
 * 21:30 on the evening the fixtures were written. Pinned, because the bench
 * reads the clock once when it mounts and dates every row against it: on the
 * real clock the same trigger reads "runs 08:40 tomorrow" tonight and
 * "runs 08:40 17 Sep" next week.
 */
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(TRIGGER_NOW);
});
afterEach(() => vi.useRealTimers());

describe("TriggersBench", () => {
  // The count is the house's, never the filtered list's: a chip that clears the
  // filter must not report the filter's own answer.
  it("offers the five kinds, and counts the whole house beside the one that means all", () => {
    render(<TriggersBench triggers={state({ kind: "sensor", shown: [door] })} />);
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

  // The bench's own clock, read once at mount and handed to every row, is what
  // makes `runs 08:40 tomorrow` mean tomorrow rather than a day in 1970.
  it("dates every row against the one clock it read when it opened", () => {
    render(<TriggersBench triggers={state({ kind: "all", shown: [bins] })} />);
    expect(
      screen.getByText("recurring · runs 08:40 tomorrow · created from conversation 20:52"),
    ).toBeInTheDocument();
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

  // "No triggers yet." is a claim about the house, and a read still in flight
  // is no evidence for it.
  it("claims nothing about an empty house until the server has answered", () => {
    render(
      <TriggersBench triggers={state({ triggers: [], shown: [], read: false, loading: true })} />,
    );
    expect(screen.queryByText("No triggers yet.")).toBeNull();
    expect(screen.getByRole("list", { name: "Triggers" })).toHaveAttribute("aria-busy", "true");
  });

  // The shape `loading` alone cannot see: a read react-query has *paused* for
  // want of a network is not in flight and has not landed, so the bench is told
  // nothing by the busy flag and everything by `read`.
  it("claims nothing about an empty house when the read is paused rather than in flight", () => {
    render(
      <TriggersBench triggers={state({ triggers: [], shown: [], read: false, loading: false })} />,
    );
    expect(screen.queryByText("No triggers yet.")).toBeNull();
    expect(screen.queryByText("No composite triggers.")).toBeNull();
  });

  // ...and a filter that empties a list the bench has never read says nothing
  // either: the chip narrows a house this bench cannot yet describe.
  it("claims nothing about a filtered list before the first read lands", () => {
    render(
      <TriggersBench
        triggers={state({ kind: "composite", triggers: [], shown: [], read: false })}
      />,
    );
    expect(screen.queryByText("No composite triggers.")).toBeNull();
    expect(screen.queryByText("No triggers yet.")).toBeNull();
  });

  it("says in the footer that a switch is a request, and that nothing here edits", () => {
    render(<TriggersBench triggers={state()} />);
    expect(screen.getByText(FOOTER_ONE)).toHaveClass("t-meta-strong");
    // There is no create/edit/delete route; a reader who does not know that
    // will hunt for a button that is not there.
    expect(screen.getByText(FOOTER_TWO)).toHaveClass("t-meta-strong");
  });

  it("announces a failed read without taking the last-known rows away", () => {
    render(<TriggersBench triggers={state({ error: "Session store unavailable" })} />);
    const region = screen.getByRole("status", { name: "Read errors" });
    expect(region).toHaveTextContent("Session store unavailable");
    expect(region).toHaveClass("t-meta-strong");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  // Mounted whether or not it has anything to say: a region inserted with its
  // text already in it can go unread, which is why its neighbours keep theirs.
  it("keeps the region mounted and out of the way while there is nothing wrong", () => {
    render(<TriggersBench triggers={state()} />);
    const region = screen.getByRole("status", { name: "Read errors" });
    expect(region).toHaveClass("sr-only");
    expect(region).toHaveTextContent("");
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

  it("gives every chip a tap target of at least 44 px and a label that stays on one line", () => {
    render(<TriggersBench triggers={state()} />);
    // 32 px of chip, 6 px of hit area above and below it.
    for (const chip of chips()) {
      expect(chip).toHaveClass("h-8");
      expect(chip).toHaveClass("after:-inset-y-1.5");
      expect(chip).toHaveClass("whitespace-nowrap");
    }
  });
});
