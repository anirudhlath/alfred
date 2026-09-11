import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { STREAMS, type StreamName } from "@/lib/streams";
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
    expect(chips[2].style.borderColor).toBe("oklch(var(--ring-text-l) 0.11 120)");
    expect(chips[2].style.color).toBe("oklch(var(--ring-text-l) 0.11 120)");
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
    expect(chips[2].style.background).toBe("oklch(0.52 0.11 120)");
    expect(chips[2].style.color).toBe("rgb(255, 255, 255)");
    expect(chips[0]).toHaveAttribute("aria-pressed", "false");
    expect(chips[0].style.borderColor).toBe("var(--muted)");
    expect(chips[0].style.color).toBe("var(--fg2)");
    expect(chips[0].style.background).toBe("transparent");
    // The colour recedes, the text does not: no chip is made translucent.
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
});
