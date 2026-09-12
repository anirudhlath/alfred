import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BenchSwitcher, benchTabId, type Bench } from "./BenchSwitcher";

/** The ids the Workshop would hand it; `benchTabId` is what both ends derive from. */
const IDS = { idBase: "bench", panelId: "bench-panel" };

function renderSwitcher(bench: Bench = "activity") {
  const onChange = vi.fn();
  render(<BenchSwitcher bench={bench} onChange={onChange} {...IDS} />);
  return { onChange, tabs: screen.getAllByRole("tab") };
}

describe("BenchSwitcher", () => {
  it("is a tablist of the four benches with the current one selected", () => {
    const { tabs } = renderSwitcher();
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Activity", "Memory", "Triggers", "System"]);
    expect(screen.getByRole("tablist", { name: "Bench" })).toBeInTheDocument();
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveAttribute("aria-selected", "false");
    // jsdom keeps `var()` in an inline style verbatim — it just never resolves
    // it — so this pins the one thing the handoff is specific about: `field`
    // under the chosen bench, nothing under the rest.
    expect(tabs[0].style.background).toBe("var(--field)");
    expect(tabs[1].style.background).toBe("transparent");
  });

  it("reports the bench that was tapped", () => {
    const { onChange } = renderSwitcher();
    fireEvent.click(screen.getByRole("tab", { name: "Triggers" }));
    expect(onChange).toHaveBeenCalledWith("triggers");
  });

  it("names the panel it drives and keeps one tab in the tab order", () => {
    const { tabs } = renderSwitcher("triggers");
    for (const tab of tabs) expect(tab).toHaveAttribute("aria-controls", IDS.panelId);
    expect(tabs.map((tab) => tab.id)).toEqual([
      benchTabId(IDS.idBase, "activity"),
      benchTabId(IDS.idBase, "memory"),
      benchTabId(IDS.idBase, "triggers"),
      benchTabId(IDS.idBase, "system"),
    ]);
    // Roving: the control is one stop, not four.
    expect(tabs.map((tab) => tab.tabIndex)).toEqual([-1, -1, 0, -1]);
  });

  it("walks the benches on the arrows, carrying the focus", () => {
    const { onChange, tabs } = renderSwitcher("memory");
    fireEvent.keyDown(tabs[1], { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("triggers");
    expect(document.activeElement).toBe(tabs[2]);

    fireEvent.keyDown(tabs[1], { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("activity");
    expect(document.activeElement).toBe(tabs[0]);
  });

  it("wraps off the front of the row", () => {
    const { onChange, tabs } = renderSwitcher("activity");
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("system");
    expect(document.activeElement).toBe(tabs[3]);
  });

  it("wraps off the end of it", () => {
    const { onChange, tabs } = renderSwitcher("system");
    fireEvent.keyDown(tabs[3], { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("activity");
    expect(document.activeElement).toBe(tabs[0]);
  });

  it("leaves every other key to the bench underneath", () => {
    const { onChange, tabs } = renderSwitcher();
    fireEvent.keyDown(tabs[0], { key: "ArrowDown" });
    expect(onChange).not.toHaveBeenCalled();
  });
});
