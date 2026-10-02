import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Gate } from "./Gate";

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

    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disables the primary while busy and says so", () => {
    render(
      <Gate
        kicker="k"
        title="t"
        primary={{ label: "Continue", onClick: () => {}, busy: true }}
      />,
    );
    const button = screen.getByRole("button", { name: "Continue" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("disables the primary when asked", () => {
    render(
      <Gate kicker="k" title="t" primary={{ label: "Finish", onClick: () => {}, disabled: true }} />,
    );
    expect(screen.getByRole("button", { name: "Finish" })).toBeDisabled();
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

  it("disables the secondary when asked", () => {
    render(
      <Gate
        kicker="k"
        title="t"
        secondary={{ label: "Do this later", onClick: () => {}, disabled: true }}
      />,
    );
    expect(screen.getByRole("button", { name: "Do this later" })).toBeDisabled();
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
