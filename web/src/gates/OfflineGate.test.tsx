import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OfflineGate } from "./OfflineGate";

describe("OfflineGate", () => {
  it("says which of the three silences this one is", () => {
    render(<OfflineGate />);
    expect(screen.getByText("offline · nothing read yet")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "No way to the house." })).toBeInTheDocument();
    expect(
      screen.getByText(
        "This phone has no connection, so I cannot ask the house who you are. Nothing is lost; I pick up where we left off as soon as there is a signal.",
      ),
    ).toBeInTheDocument();
  });

  it("sets its kicker in the meta a reader can take on its own", () => {
    // The kicker is the line that says which silence this is, so it takes
    // `.t-meta-strong`'s --fg2 rather than `.t-meta`'s --muted (index.css).
    render(<OfflineGate />);
    const kicker = screen.getByText("offline · nothing read yet");
    expect(kicker).toHaveClass("t-meta-strong");
    expect(kicker).not.toHaveClass("t-meta");
  });

  it("offers no button at all", () => {
    // Deviation 5. A `Try again` would send a request — react-query is not
    // paused on a launch that began offline — but one that can only fail the
    // way the read already did, leaving this gate where it is. And the moment
    // there is a signal, AuthGate asks again by itself.
    render(<OfflineGate />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
