import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StandaloneGate, type StandaloneGateProps } from "./StandaloneGate";

// Alfred.dc.html:670, verbatim.
const KICKER = "reaching you · 5 of 6 · opened from the home screen";
const TITLE = "You came in the front way.";
const BODY =
  "From here I may ask iOS to let me reach you when the app is closed. Only for what cannot wait: an approval with a fuse, the house needing you. Never routine chatter.";
const STEPS = [
  "Open in Safari",
  "Sign in with Face ID",
  "Add to Home Screen",
  "Open from the home screen",
  "Let Alfred reach you",
  "Allow in iOS",
];
const FOOT =
  "iOS asks once. Decline, and the switch moves to Settings; I shall not raise it again.";

// A local wall-clock time, so `hhmm` reads 07:02 in any time zone.
const SIGNED_IN_AT = new Date(2026, 9, 3, 7, 2, 41).toISOString();

function gate(props: Partial<StandaloneGateProps> = {}) {
  return (
    <StandaloneGate
      signedInAt={SIGNED_IN_AT}
      busy={false}
      onAllow={() => {}}
      onDismiss={() => {}}
      {...props}
    />
  );
}

function row(label: string): HTMLElement {
  const li = screen.getByText(label).closest("li");
  if (!li) throw new Error(`no step row for ${label}`);
  return li;
}

beforeEach(() => {
  vi.stubGlobal("location", { hostname: "alfred.example.com", pathname: "/" });
});

describe("StandaloneGate", () => {
  it("says the handoff's words", () => {
    render(gate());
    expect(screen.getByText(KICKER)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();
    expect(screen.getByText(BODY)).toBeInTheDocument();
    for (const label of STEPS) expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
    expect(screen.getByRole("button", { name: "Let me reach you" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not yet" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(FOOT);
  });

  it("marks steps one to four done and five current", () => {
    render(gate());
    expect(STEPS.map((label) => row(label).getAttribute("data-step-state"))).toEqual([
      "done",
      "done",
      "done",
      "done",
      "current",
      "todo",
    ]);
  });

  it("names this host and the sign-in time", () => {
    render(gate());
    expect(within(row("Open in Safari")).getByText("alfred.example.com")).toBeInTheDocument();
    expect(within(row("Sign in with Face ID")).getByText("07:02")).toBeInTheDocument();
    expect(within(row("Open from the home screen")).getByText("now")).toBeInTheDocument();
  });

  it("leaves the sign-in time out until it is known", () => {
    // The sessions read has not landed: no stamp, and not `--:--` either — the
    // step is done, only its time is not yet known.
    render(gate({ signedInAt: null }));
    expect(row("Sign in with Face ID")).not.toHaveTextContent(/\d|--:--/);
    // No empty meta element either, holding a gap where the time would go.
    expect(row("Sign in with Face ID").querySelector(".t-meta")).toBeNull();
  });

  it("asks from the primary and holds it while asking", async () => {
    const user = userEvent.setup();
    const onAllow = vi.fn();
    const { rerender } = render(gate({ onAllow }));
    const button = screen.getByRole("button", { name: "Let me reach you" });
    expect(button).not.toHaveAttribute("aria-busy");

    await user.click(button);
    expect(onAllow).toHaveBeenCalledTimes(1);

    rerender(gate({ onAllow, busy: true }));
    expect(button).toHaveAttribute("aria-busy", "true");
    await user.click(button);
    expect(onAllow).toHaveBeenCalledTimes(1);
  });

  it("Not yet dismisses", async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    render(gate({ onDismiss }));

    await user.click(screen.getByRole("button", { name: "Not yet" }));

    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
