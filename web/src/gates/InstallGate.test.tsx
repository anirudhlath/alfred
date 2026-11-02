import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { InstallGate } from "./InstallGate";

// Alfred.dc.html:669, verbatim.
const KICKER = "reaching you · 3 of 6 · in Safari";
const TITLE = "Add me to the home screen.";
const BODY =
  "Safari will not let me ask to notify you until I have been added there, and there is no button I can show for it. It is one gesture, at the bottom of the screen.";
const STEPS = [
  "Tap Share, below",
  'Scroll to "Add to Home Screen"',
  "Tap Add, then open Alfred from there",
];
const FOOT = "This tab can be closed once you have. Nothing is lost; I pick up where we left off.";

function stateOf(label: string): string | null | undefined {
  return screen.getByText(label).closest("[data-step-state]")?.getAttribute("data-step-state");
}

describe("InstallGate", () => {
  it("says the handoff's words", () => {
    render(<InstallGate onDismiss={() => {}} />);
    expect(screen.getByText(KICKER)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();
    expect(screen.getByText(BODY)).toBeInTheDocument();
    for (const label of STEPS) expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByRole("status")).toHaveTextContent(FOOT);
    // The pointer at the bottom, towards Safari's Share button.
    expect(screen.getByText("Share").closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it("offers no primary, and Not now dismisses", async () => {
    // There is no button the page can show for Add to Home Screen: Safari keeps
    // that gesture to itself, so the only thing to press is the way out.
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    render(<InstallGate onDismiss={onDismiss} />);
    expect(screen.getAllByRole("button")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Not now" }));

    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("marks the first step current", () => {
    render(<InstallGate onDismiss={() => {}} />);
    expect(STEPS.map(stateOf)).toEqual(["current", "todo", "todo"]);
  });
});
