import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UnansweredGate } from "./UnansweredGate";

const KICKER = "alfred.example.com · unknown since --:--";
const BODY =
  "I asked it who you are and had no answer, so I cannot say whether you are signed in. Nothing is lost; I ask again when you try, and each time you come back.";

beforeEach(() => {
  vi.stubGlobal("location", { hostname: "alfred.example.com", pathname: "/" });
});

describe("UnansweredGate", () => {
  it("says the house did not answer, and claims nothing about the session", () => {
    render(<UnansweredGate asking={false} onTryAgain={() => {}} />);
    expect(screen.getByText(KICKER)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "The house is not answering." }),
    ).toBeInTheDocument();
    expect(screen.getByText(BODY)).toBeInTheDocument();
    // The sign-in gate's kicker in this state was `<host> · signed out`: a
    // claim about the session, made with no answer from the house.
    expect(screen.queryByText(/signed out/)).toBeNull();
  });

  it("sets its kicker in the meta a reader can take on its own", () => {
    // It is the line that says which silence this is, as on the offline gate.
    render(<UnansweredGate asking={false} onTryAgain={() => {}} />);
    const kicker = screen.getByText(KICKER);
    expect(kicker).toHaveClass("t-meta-strong");
    expect(kicker).not.toHaveClass("t-meta");
  });

  it("offers one thing, Try again, and it asks", async () => {
    // Unlike the offline gate's, a retry here can succeed the moment the house
    // answers, so the gate has a button.
    const user = userEvent.setup();
    const onTryAgain = vi.fn();
    render(<UnansweredGate asking={false} onTryAgain={onTryAgain} />);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    const button = screen.getByRole("button", { name: "Try again" });
    expect(button).not.toHaveAttribute("aria-disabled");

    await user.click(button);

    expect(onTryAgain).toHaveBeenCalledTimes(1);
  });

  it("holds Try again while a read is in flight", async () => {
    const user = userEvent.setup();
    const onTryAgain = vi.fn();
    render(<UnansweredGate asking onTryAgain={onTryAgain} />);
    const button = screen.getByRole("button", { name: "Try again" });
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();

    await user.click(button);

    expect(onTryAgain).not.toHaveBeenCalled();
  });
});
