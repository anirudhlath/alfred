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

  it("renders children between the body and the footer", () => {
    render(
      <Gate kicker="k" title="t" body="b">
        <p>step list goes here</p>
      </Gate>,
    );
    expect(screen.getByText("step list goes here")).toBeInTheDocument();
  });
});
