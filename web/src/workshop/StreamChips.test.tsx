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
    for (const chip of chips) {
      expect(chip).toHaveAttribute("aria-pressed", "false");
      expect(chip.style.opacity).toBe("1");
      expect(chip.style.background).toBe("transparent");
    }
    expect(chips[2].style.borderColor).toBe("oklch(0.62 0.11 120)");
    expect(chips[2].style.color).toBe("oklch(0.62 0.11 120)");
  });

  it("fills the solo chip with its ring and dims the rest", () => {
    render(<StreamChips counts={counts} solo="events" onSolo={() => {}} />);
    const chips = screen.getAllByRole("button");
    expect(chips[2]).toHaveAttribute("aria-pressed", "true");
    expect(chips[2].style.background).toBe("oklch(0.62 0.11 120)");
    expect(chips[2].style.color).toBe("rgb(255, 255, 255)");
    expect(chips[2].style.opacity).toBe("1");
    expect(chips[0]).toHaveAttribute("aria-pressed", "false");
    expect(chips[0].style.opacity).toBe("0.35");
    expect(chips[0].style.background).toBe("transparent");
  });

  it("tapping a chip solos it; tapping the solo chip clears", () => {
    const onSolo = vi.fn();
    const { rerender } = render(<StreamChips counts={counts} solo={null} onSolo={onSolo} />);
    fireEvent.click(screen.getByRole("button", { name: "HS 18" }));
    expect(onSolo).toHaveBeenLastCalledWith("home_state");
    rerender(<StreamChips counts={counts} solo="home_state" onSolo={onSolo} />);
    fireEvent.click(screen.getByRole("button", { name: "HS 18" }));
    expect(onSolo).toHaveBeenLastCalledWith(null);
  });
});
