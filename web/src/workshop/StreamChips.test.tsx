import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RING_COUNT_ON_OPACITY, ringFill, ringText, STREAM_INFO, STREAMS, type StreamName } from "@/lib/streams";
import { StreamChips } from "./StreamChips";

const counts = Object.fromEntries(STREAMS.map((name, i) => [name, i * 3])) as Record<StreamName, number>;

describe("StreamChips", () => {
  it("shows eight chips, monogram over count, none pressed", () => {
    render(<StreamChips counts={counts} solo={null} onSolo={() => {}} />);
    const chips = screen.getAllByRole("button");
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "UR0", "AL3", "EV6", "AC9", "RX12", "NT15", "HS18", "HR21",
    ]);
    for (const chip of chips) expect(chip).toHaveAttribute("aria-pressed", "false");
    // Nothing is soloed, so every chip wears its own hue at full strength.
    // `ringText(120)` rather than the string it returns: `lib/streams.test.ts`
    // owns that spelling, and a second copy of it here would have to be found
    // and edited by whoever changes the ring.
    expect(chips[2].style.borderColor).toBe(ringText(120));
    expect(chips[2].style.color).toBe(ringText(120));
    expect(chips[2].style.background).toBe("transparent");
  });

  it("says what each monogram stands for", () => {
    render(<StreamChips counts={counts} solo={null} onSolo={() => {}} />);
    expect(screen.getAllByRole("button").map((chip) => chip.getAttribute("aria-label"))).toEqual([
      "UR user requests, 0",
      "AL user responses, 3",
      "EV events, 6",
      "AC actions, 9",
      "RX reflex observations, 12",
      "NT notifications, 15",
      "HS home state, 18",
      "HR home action results, 21",
    ]);
  });

  it("fills the solo chip with its ring and lets the rest recede without dimming the count", () => {
    render(<StreamChips counts={counts} solo="events" onSolo={() => {}} />);
    const chips = screen.getAllByRole("button");
    expect(chips[2]).toHaveAttribute("aria-pressed", "true");
    expect(chips[2].style.background).toBe(ringFill(120));
    expect(chips[2].style.color).toBe("rgb(255, 255, 255)");
    expect(chips[0]).toHaveAttribute("aria-pressed", "false");
    expect(chips[0].style.borderColor).toBe("var(--muted)");
    expect(chips[0].style.color).toBe("var(--fg2)");
    expect(chips[0].style.background).toBe("transparent");
    // A tripwire, not a measurement: the component writes no `opacity` at all,
    // so this asserts the *absence* of a property and cannot fail on any
    // present-day edit. It is here because the dimming it forbids is the thing
    // that was tried first — 35 % opacity, which took the count to 1.31:1 in
    // the light theme — and anyone reaching for it again will reach for this
    // property. Delete it the day the chips stop being the only data they carry.
    for (const chip of chips) expect(chip.style.opacity).toBe("");
  });

  it("tapping a chip solos it; tapping the solo chip clears", () => {
    const onSolo = vi.fn();
    const { rerender } = render(<StreamChips counts={counts} solo={null} onSolo={onSolo} />);
    fireEvent.click(screen.getByRole("button", { name: "HS home state, 18" }));
    expect(onSolo).toHaveBeenLastCalledWith("home_state");
    rerender(<StreamChips counts={counts} solo="home_state" onSolo={onSolo} />);
    fireEvent.click(screen.getByRole("button", { name: "HS home state, 18" }));
    expect(onSolo).toHaveBeenLastCalledWith(null);
  });

  describe("compact", () => {
    it("is 36 px and keeps every hue while one is soloed", () => {
      render(<StreamChips compact counts={counts} solo="events" onSolo={() => {}} />);
      const chips = screen.getAllByRole("button");
      STREAMS.forEach((name, i) => {
        expect(chips[i]).toHaveClass("h-9");
        expect(chips[i].style.borderColor).toBe(ringText(STREAM_INFO[name].hue));
      });
      // R19: the others keep their ink, where the phone's would have gone to --fg2.
      expect(chips[0].style.color).toBe(ringText(30));
      expect(chips[0].style.background).toBe("transparent");
      expect(chips[2]).toHaveAttribute("aria-pressed", "true");
      expect(chips[2].style.background).toBe(ringFill(120));
      expect(chips[2].style.color).toBe("rgb(255, 255, 255)");
      for (const chip of chips) expect(chip.style.opacity).toBe("");
    });

    it("sets the count beside the monogram, and lets a narrow chip stack it", () => {
      render(<StreamChips compact counts={counts} solo={null} onSolo={() => {}} />);
      const chip = screen.getByRole("button", { name: "EV events, 6" });
      expect(chip).toHaveClass("flex-wrap", "gap-x-1", "min-w-0", "overflow-hidden");
      expect(chip).not.toHaveClass("flex-col");
    });

    it("holds the count at the opacity AA needs, off and on", () => {
      // The mock's .7 and .8 are under AA in every case. `test/contrast.test.ts` holds
      // what these two resolve to, and jsdom resolves no `var()`, so neither file is
      // the whole check alone.
      render(<StreamChips compact counts={counts} solo="events" onSolo={() => {}} />);
      expect(screen.getByText("3").style.opacity).toBe("var(--ring-count-opacity)");
      expect(screen.getByText("6").style.opacity).toBe(String(RING_COUNT_ON_OPACITY));
    });

    it("renders trailing after the eight, in a group that does not shrink", () => {
      render(
        <StreamChips
          compact
          counts={counts}
          solo={null}
          onSolo={() => {}}
          trailing={<button type="button">All</button>}
        />,
      );
      const buttons = screen.getAllByRole("button");
      expect(buttons).toHaveLength(9);
      expect(buttons[8]).toHaveTextContent("All");
      expect(buttons[8].parentElement).toHaveClass("flex", "shrink-0", "gap-1.5");
      // The group is what R20's 352 px is measured from: 240 + gap + the two buttons.
      expect(buttons[0].parentElement).toHaveClass("flex", "min-w-0", "flex-[1_1_240px]", "gap-1.5");
      expect(buttons[0].parentElement?.parentElement).toHaveClass("flex", "flex-wrap", "gap-1.5");
    });

    it("says the same names and toggles the same way", () => {
      const onSolo = vi.fn();
      const { rerender } = render(<StreamChips compact counts={counts} solo={null} onSolo={onSolo} />);
      expect(screen.getAllByRole("button").map((chip) => chip.getAttribute("aria-label"))).toEqual([
        "UR user requests, 0",
        "AL user responses, 3",
        "EV events, 6",
        "AC actions, 9",
        "RX reflex observations, 12",
        "NT notifications, 15",
        "HS home state, 18",
        "HR home action results, 21",
      ]);
      fireEvent.click(screen.getByRole("button", { name: "HS home state, 18" }));
      expect(onSolo).toHaveBeenLastCalledWith("home_state");
      rerender(<StreamChips compact counts={counts} solo="home_state" onSolo={onSolo} />);
      expect(screen.getByRole("button", { name: "HS home state, 18" })).toHaveAttribute("aria-pressed", "true");
      fireEvent.click(screen.getByRole("button", { name: "HS home state, 18" }));
      expect(onSolo).toHaveBeenLastCalledWith(null);
    });
  });
});
