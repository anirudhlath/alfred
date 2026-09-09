import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DndRow } from "./DndRow";
import { Headline } from "./Headline";
import { OfflineNote } from "./OfflineNote";

const at2114 = new Date(2026, 8, 7, 21, 14);

describe("Headline", () => {
  it("is the page's one heading", () => {
    render(<Headline text="Listening, sir." />);
    const heading = screen.getByRole("heading", { level: 1, name: "Listening, sir." });
    expect(heading).toHaveClass("t-headline");
  });
});

describe("OfflineNote", () => {
  it("stamps when the house was last reachable, and announces it", () => {
    render(<OfflineNote online={false} reconnecting={false} lastTrueAt={at2114} />);
    // A status message: the socket dropping is news, not decoration.
    expect(screen.getByRole("status")).toHaveTextContent(
      /^No connection to the house since 21:14\. Everything below is last-known\. Sending is paused\.$/,
    );
  });

  it("says it is still trying while reconnecting", () => {
    render(<OfflineNote online={false} reconnecting lastTrueAt={at2114} />);
    expect(
      screen.getByText("Trying again. Everything below was last true at 21:14."),
    ).toBeInTheDocument();
  });

  it("prints an unknown clock rather than a made-up one", () => {
    render(<OfflineNote online={false} reconnecting={false} lastTrueAt={null} />);
    expect(screen.getByText(/since --:--\./)).toBeInTheDocument();
  });

  it("keeps its live region mounted, and empty, while online", () => {
    // Mounted before there is anything to say: VoiceOver can miss a live
    // region that arrives with its text already in it.
    render(<OfflineNote online reconnecting={false} lastTrueAt={at2114} />);
    const region = screen.getByRole("status");
    expect(region).toBeEmptyDOMElement();
    // Out of flow, or the header's gap would open around an empty box.
    expect(region).toHaveClass("sr-only");
    expect(region).not.toHaveClass("mt-2");
  });
});

describe("DndRow", () => {
  it("names the hour quiet ends and how much is waiting", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(
      <DndRow until={new Date(2026, 8, 8, 8, 30).toISOString()} heldCount={2} onOpen={onOpen} />,
    );

    const row = screen.getByRole("button", { name: /Do-not-disturb until 08:30/ });
    expect(row).toHaveTextContent("2 held ›");

    await user.click(row);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("says there is no expiry when there is none", () => {
    render(<DndRow until={null} heldCount={0} onOpen={() => {}} />);
    expect(
      screen.getByRole("button", { name: /Do-not-disturb · no expiry/ }),
    ).toHaveTextContent("0 held ›");
  });

  it("treats an unparseable expiry as no expiry", () => {
    render(<DndRow until="whenever" heldCount={1} onOpen={() => {}} />);
    expect(screen.getByRole("button", { name: /Do-not-disturb · no expiry/ })).toBeInTheDocument();
  });

  it("is a 44 px tap target", () => {
    render(<DndRow until={null} heldCount={0} onOpen={() => {}} />);
    expect(screen.getByRole("button")).toHaveClass("min-h-11");
  });
});
