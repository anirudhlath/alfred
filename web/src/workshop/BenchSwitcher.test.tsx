import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BenchSwitcher } from "./BenchSwitcher";

describe("BenchSwitcher", () => {
  it("is a tablist of the four benches with the current one selected", () => {
    render(<BenchSwitcher bench="activity" onChange={() => {}} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Activity", "Memory", "Triggers", "System"]);
    expect(screen.getByRole("tablist", { name: "Bench" })).toBeInTheDocument();
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveAttribute("aria-selected", "false");
  });

  it("reports the bench that was tapped", () => {
    const onChange = vi.fn();
    render(<BenchSwitcher bench="activity" onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: "Triggers" }));
    expect(onChange).toHaveBeenCalledWith("triggers");
  });
});
