import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FeedRow } from "@/lib/feed";
import { hhmmss } from "@/lib/format";
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
    expect(monogram.style.background).toBe("oklch(0.62 0.11 210)");
    expect(screen.getByText("observed media_player.tv · acted")).toBeInTheDocument();
    expect(
      screen.getByText(
        `${hhmmss(1788800280000)} · home.light_set · request 4b1d · decision "movie started, evening, user home"`,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { expanded: false })).toBeInTheDocument();
    expect(screen.queryByText("Only RX")).toBeNull();
  });

  it("opens to the raw event and the pills, offering why only when it is given", () => {
    const onWhy = vi.fn();
    const { rerender } = render(
      <ul>
        <EventRow row={row} expanded onToggle={() => {}} onSolo={() => {}} />
      </ul>,
    );
    expect(screen.getByRole("button", { expanded: true })).toBeInTheDocument();
    expect(screen.getByText(/"tool_name": "home.light_set"/)).toBeInTheDocument();
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
