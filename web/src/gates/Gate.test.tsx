import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Gate } from "./Gate";

/** A gate whose primary goes busy on its own press, as Sign in and Continue do. */
function BusyOnPress() {
  const [busy, setBusy] = useState(false);
  return (
    <Gate
      kicker="k"
      title="t"
      primary={{ label: "Sign in with Face ID", onClick: () => setBusy(true), busy }}
    />
  );
}

/** A gate whose secondary is taken out of use by its own press. */
function HeldOnPress() {
  const [held, setHeld] = useState(false);
  return (
    <Gate
      kicker="k"
      title="t"
      secondary={{ label: "Do this later", onClick: () => setHeld(true), disabled: held }}
    />
  );
}

describe("Gate", () => {
  it("lays out kicker, title, body and foot", () => {
    render(
      <Gate
        kicker="first run · alfred.example.com"
        title="Good evening. I am Alfred."
        body="This device will hold the only key to the house."
        foot="The passkey never leaves the phone. Nothing here phones home."
      />,
    );

    expect(screen.getByText("first run · alfred.example.com")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Good evening. I am Alfred." }),
    ).toBeInTheDocument();
    expect(screen.getByText("This device will hold the only key to the house.")).toBeInTheDocument();
    expect(
      screen.getByText("The passkey never leaves the phone. Nothing here phones home."),
    ).toBeInTheDocument();
  });

  it("sets the kicker in .t-meta unless the gate asks for the strong meta", () => {
    const { rerender } = render(<Gate kicker="k" title="t" />);
    expect(screen.getByText("k")).toHaveClass("t-meta");

    rerender(<Gate kicker="k" title="t" strongKicker />);
    expect(screen.getByText("k")).toHaveClass("t-meta-strong");
    expect(screen.getByText("k")).not.toHaveClass("t-meta");
  });

  it("renders the dot field, hidden from assistive tech", () => {
    const { container } = render(<Gate kicker="k" title="t" />);
    const field = container.querySelector(".gate-field");
    expect(field).not.toBeNull();
    expect(field).toHaveAttribute("aria-hidden", "true");
  });

  it("calls the primary action", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Gate kicker="k" title="t" primary={{ label: "Create passkey with Face ID", onClick }} />,
    );

    const button = screen.getByRole("button", { name: "Create passkey with Face ID" });
    expect(button).not.toHaveAttribute("aria-disabled");
    expect(button).not.toHaveAttribute("aria-busy");
    expect(button).toHaveStyle({ background: "var(--ink)", color: "var(--paper)" });

    await user.click(button);

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("holds the primary while busy and says so, without taking it out of reach", async () => {
    // `aria-disabled` and a press that does nothing, never `disabled` — see the
    // secondary's test below. Still ink on paper: a busy button is answering
    // the press it took, not unavailable.
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Gate kicker="k" title="t" primary={{ label: "Continue", onClick, busy: true }} />);
    const button = screen.getByRole("button", { name: "Continue" });

    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
    expect(button).toHaveStyle({ background: "var(--ink)", color: "var(--paper)" });
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("keeps focus on the primary through the press that makes it busy", async () => {
    // A native `disabled` here threw focus to <body> mid-sign-in, measured in
    // Chromium, so a keyboard or switch user lost their place on the one control
    // the gate has.
    const user = userEvent.setup();
    render(<BusyOnPress />);
    const button = screen.getByRole("button", { name: "Sign in with Face ID" });

    await user.click(button);

    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveFocus();
  });

  it("holds an unavailable primary, dimmed by token and edged so it can still be found", async () => {
    // The handoff draws no unavailable state for a gate's buttons, so this one
    // is the house's: a `--surface` fill with an `--fg2` label, and an inset
    // `--muted` edge because `--surface` alone barely leaves the page. The
    // pairs are measured in `test/contrast.test.ts`. Never by opacity.
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(
      <Gate kicker="k" title="t" primary={{ label: "Finish", onClick, disabled: true }} />,
    );
    const button = screen.getByRole("button", { name: "Finish" });

    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).not.toHaveAttribute("aria-busy");
    expect(button).not.toBeDisabled();
    expect(button).toHaveStyle({
      background: "var(--surface)",
      color: "var(--fg2)",
      boxShadow: "inset 0 0 0 1px var(--muted)",
    });
    expect(button.className).not.toMatch(/opacity/);
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();

    rerender(<Gate kicker="k" title="t" primary={{ label: "Finish", onClick, disabled: false }} />);
    expect(button).not.toHaveAttribute("aria-disabled");
    expect(button).toHaveStyle({ background: "var(--ink)", color: "var(--paper)" });
    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders a secondary action only when given one", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(<Gate kicker="k" title="t" />);
    expect(screen.queryByRole("button")).toBeNull();

    rerender(<Gate kicker="k" title="t" secondary={{ label: "Do this later", onClick }} />);
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("holds an unavailable secondary the way it holds a waiting one", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Gate kicker="k" title="t" secondary={{ label: "Do this later", onClick, disabled: true }} />,
    );
    const button = screen.getByRole("button", { name: "Do this later" });

    expect(button).toHaveAttribute("aria-disabled", "true");
    // Unavailable, not waiting on anything: nothing is in flight to announce.
    expect(button).not.toHaveAttribute("aria-busy");
    expect(button).not.toBeDisabled();
    expect(button).toHaveStyle({ color: "var(--fg2)" });
    expect(button.className).not.toMatch(/opacity/);
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("keeps focus on the secondary through the press that takes it out of use", async () => {
    const user = userEvent.setup();
    render(<HeldOnPress />);
    const button = screen.getByRole("button", { name: "Do this later" });

    await user.click(button);

    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveFocus();
  });

  it("holds the secondary while its request is in flight, without taking it out of reach", async () => {
    // `aria-disabled` and a press that does nothing, never `disabled`: a control
    // that disables itself under the finger that pressed it throws focus to
    // <body> mid-action. Dimmed by a token, never by opacity.
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(
      <Gate kicker="k" title="t" secondary={{ label: "Try again", onClick, waiting: true }} />,
    );
    const button = screen.getByRole("button", { name: "Try again" });
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
    expect(button).toHaveStyle({ color: "var(--fg2)" });
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();

    rerender(
      <Gate kicker="k" title="t" secondary={{ label: "Try again", onClick, waiting: false }} />,
    );
    expect(button).not.toHaveAttribute("aria-disabled");
    expect(button).not.toHaveAttribute("aria-busy");
    expect(button).toHaveStyle({ color: "var(--fg)" });
    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("speaks the foot line: it is a status region", () => {
    const { rerender } = render(<Gate kicker="k" title="t" foot="Stored encrypted at rest." />);
    expect(screen.getByRole("status")).toHaveTextContent("Stored encrypted at rest.");

    rerender(<Gate kicker="k" title="t" foot="Face ID was cancelled." />);
    expect(screen.getByRole("status")).toHaveTextContent("Face ID was cancelled.");
  });

  it("renders children between the body and the footer", () => {
    render(
      <Gate kicker="k" title="t" body="b">
        <p>step list goes here</p>
      </Gate>,
    );
    expect(screen.getByText("step list goes here")).toBeInTheDocument();
  });
});
