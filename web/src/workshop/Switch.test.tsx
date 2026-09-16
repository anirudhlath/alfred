import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Switch } from "./Switch";

/**
 * One whole class, not a substring: `toContain("min-h-14")` also passes on
 * `min-h-140` and on a class that merely starts the same way, which is a
 * promise about the frame that a typo could keep.
 */
const hasClass = (element: Element | null | undefined, name: string): boolean =>
  (element?.className ?? "").split(/\s+/).includes(name);


const knob = () => screen.getByTestId("switch-knob");
const control = () => screen.getByRole("switch");

function renderSwitch(props: Partial<Parameters<typeof Switch>[0]> = {}) {
  const onToggle = vi.fn();
  render(
    <Switch
      on={false}
      label="Porch light"
      inert={false}
      busy={false}
      describedBy={undefined}
      onToggle={onToggle}
      {...props}
    />,
  );
  return onToggle;
}

describe("Switch", () => {
  it("reports the state it was given and moves the knob to match", () => {
    const { unmount } = render(
      <Switch on={false} label="Porch light" inert={false} busy={false} describedBy={undefined} onToggle={vi.fn()} />,
    );
    expect(control()).not.toBeChecked();
    expect(knob()).toHaveStyle({ left: "3px", background: "var(--fg2)" });
    expect(control()).toHaveStyle({ background: "var(--line)" });
    unmount();

    render(
      <Switch on label="Porch light" inert={false} busy={false} describedBy={undefined} onToggle={vi.fn()} />,
    );
    expect(control()).toBeChecked();
    expect(knob()).toHaveStyle({ left: "23px", background: "var(--on-accent)" });
    expect(control()).toHaveStyle({ background: "var(--accent)" });
  });

  // 1.4.11: the *boundary* is the outermost pixel of the control, and that is
  // this inset edge rather than either track fill — which is what lets one
  // switch sit on the page and on a `--surface` card with one pair measured.
  it("carries its own edge in both positions", () => {
    const { rerender } = render(
      <Switch on={false} label="Porch light" inert={false} busy={false} describedBy={undefined} onToggle={vi.fn()} />,
    );
    expect(control()).toHaveStyle({ boxShadow: "inset 0 0 0 1px var(--muted)" });
    rerender(
      <Switch on label="Porch light" inert={false} busy={false} describedBy={undefined} onToggle={vi.fn()} />,
    );
    expect(control()).toHaveStyle({ boxShadow: "inset 0 0 0 1px var(--muted)" });
  });

  it("asks its caller, and never moves itself", () => {
    const onToggle = renderSwitch();
    fireEvent.click(control());
    expect(onToggle).toHaveBeenCalledTimes(1);
    // `on` is the caller's to change: a switch that moved itself would be
    // claiming an outcome the server has not reported.
    expect(control()).not.toBeChecked();
  });

  // A disabled control cannot take focus, so its description is never
  // announced and a reader hears nothing about the request just sent.
  it("refuses a press without taking focus away from itself", () => {
    const onToggle = renderSwitch({ inert: true, busy: true, describedBy: "note" });
    expect(control()).toHaveAttribute("aria-disabled", "true");
    expect(control()).toHaveAttribute("aria-busy", "true");
    expect(control()).toHaveAttribute("aria-describedby", "note");
    expect(control()).not.toBeDisabled();
    control().focus();
    fireEvent.click(control());
    expect(onToggle).not.toHaveBeenCalled();
    // The whole reason the refusal lives in the handler rather than in
    // `disabled`: the note this press produced is the control's own
    // `aria-describedby`, and a description is announced on focus. A control
    // that threw focus to `<body>` — by being disabled, or by blurring itself
    // — would leave a screen reader with nothing at all about what it just
    // refused. `not.toBeDisabled()` above does not see that happen.
    expect(document.activeElement).toBe(control());
  });

  // Two different facts: a Triggers row whose write was already refused is
  // inert with nothing in flight, and must not also claim to be waiting.
  it("can be inert without being busy", () => {
    const onToggle = renderSwitch({ inert: true, busy: false });
    expect(control()).toHaveAttribute("aria-disabled", "true");
    expect(control()).not.toHaveAttribute("aria-busy");
    fireEvent.click(control());
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("says neither thing when it is neither", () => {
    renderSwitch();
    expect(control()).not.toHaveAttribute("aria-disabled");
    expect(control()).not.toHaveAttribute("aria-busy");
    expect(control()).not.toHaveAttribute("aria-describedby");
  });

  // 44 px of target from a 32 px control: the `after` box reaches 6 px above
  // and below, which is what `TriggersBench` and `SystemBench` both measure.
  it("reaches a thumb's height without growing", () => {
    renderSwitch();
    expect(hasClass(control(), "h-8")).toBe(true);
    // The `after` box is the target: 6 px above and below a 32 px control, and
    // the full width of it.
    expect(hasClass(control(), "after:-inset-y-1.5")).toBe(true);
    expect(hasClass(control(), "after:inset-x-0")).toBe(true);
    // Without content the pseudo-element is never generated and the box is not
    // there at all.
    expect(hasClass(control(), "after:content-['']")).toBe(true);
  });

  // Inside a form — which the Workshop is not today and task 9 may well be — a
  // button with no explicit type submits it.
  it("is a button that does nothing but call back", () => {
    renderSwitch();
    expect(control()).toHaveAttribute("type", "button");
  });

  it("is named for the thing it switches, and hides its knob from the reader", () => {
    renderSwitch({ label: "Do-not-disturb" });
    expect(screen.getByRole("switch", { name: "Do-not-disturb" })).toBeInTheDocument();
    expect(knob()).toHaveAttribute("aria-hidden", "true");
    // `left` is what moves the knob, and only an absolutely-positioned one moves.
    expect(hasClass(knob(), "absolute")).toBe(true);
    expect(hasClass(knob(), "rounded-full")).toBe(true);
  });
});
