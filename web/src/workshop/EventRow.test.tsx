import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FeedRow } from "@/lib/feed";
import { hhmmss } from "@/lib/format";
import { ringFill } from "@/lib/streams";
import { reflexObservationsPage } from "@/test/fixtures";
import { EventRow } from "./EventRow";

const observation = reflexObservationsPage.entries[2]; // obs-1: acted on media_player.tv
const row: FeedRow = {
  stream: "reflex_observations",
  entry: observation,
  key: `reflex_observations:${observation.id}`,
};

describe("EventRow", () => {
  it("shows the monogram in the stream's ring, the summary, and the meta stamped to the second", () => {
    render(
      <ul>
        <EventRow row={row} expanded={false} onToggle={() => {}} onSolo={() => {}} />
      </ul>,
    );
    const monogram = screen.getByTestId("monogram");
    expect(monogram).toHaveTextContent("RX");
    // `ringFill(210)`, the way the stamp beside it calls `hhmmss`: the spelling
    // of the ring is `lib/streams.test.ts`'s to pin, not a second copy's.
    expect(monogram.style.background).toBe(ringFill(210));
    // The tile is a picture of the stream; the words beside it are what is read.
    // `\s*` because jsdom loads no stylesheet, so it cannot see that the
    // sr-only span is out of flow and does not separate it from what follows.
    expect(monogram).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("reflex observations,")).toHaveClass("sr-only");
    expect(screen.getByRole("button", { expanded: false })).toHaveAccessibleName(
      /^reflex observations,\s*observed media_player\.tv · acted/,
    );
    expect(screen.getByText("observed media_player.tv · acted")).toBeInTheDocument();
    expect(
      screen.getByText(
        `${hhmmss(1788800280000)} · home.light_set · request 4b1d · decision "movie started, evening, user home"`,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Only RX")).toBeNull();
    expect(screen.queryByText(/"tool_name"/)).toBeNull();
    // Nothing to control while shut: the id it would name is not in the document.
    expect(screen.getByRole("button")).not.toHaveAttribute("aria-controls");
  });

  it("opens to the raw event and the pills, offering why only when it is given", () => {
    const onWhy = vi.fn();
    const { rerender } = render(
      <ul>
        <EventRow row={row} expanded onToggle={() => {}} onSolo={() => {}} />
      </ul>,
    );
    // The row's button names the panel it opened, and only while it is open.
    const opened = screen.getByRole("button", { expanded: true });
    const payload = screen.getByText(/"tool_name": "home.light_set"/);
    expect(opened.getAttribute("aria-controls")).toBe(payload.parentElement?.id);
    expect(opened.getAttribute("aria-controls")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Only RX" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Why · causal thread" })).toBeNull();

    rerender(
      <ul>
        <EventRow row={row} expanded onToggle={() => {}} onSolo={() => {}} onWhy={onWhy} />
      </ul>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Why · causal thread" }));
    expect(onWhy).toHaveBeenCalledTimes(1);
  });

  it("toggles on the row, solos on the pill", () => {
    const onToggle = vi.fn();
    const onSolo = vi.fn();
    render(
      <ul>
        <EventRow row={row} expanded onToggle={onToggle} onSolo={onSolo} />
      </ul>,
    );
    fireEvent.click(screen.getByText("observed media_player.tv · acted"));
    expect(onToggle).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Only RX" }));
    expect(onSolo).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  // An id the client cannot read is 0 ms, and 0 ms is a real instant: rendered
  // straight it reads as a 1970 clock, which is a lie the stamp must not tell.
  it("stamps an id it cannot read as --:--:--, not a 1970 clock", () => {
    const broken: FeedRow = {
      stream: "events",
      entry: { id: "garbage", event: { source: "mqtt-bridge" } },
      key: "events:garbage",
    };
    render(
      <ul>
        <EventRow row={broken} expanded={false} onToggle={() => {}} onSolo={() => {}} />
      </ul>,
    );
    expect(screen.getByText(/^--:--:-- · /)).toHaveTextContent("--:--:-- · source mqtt-bridge");
  });
});
