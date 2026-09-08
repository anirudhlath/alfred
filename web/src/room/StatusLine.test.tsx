import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { firstRunOverviewFixture, overviewFixture } from "@/test/fixtures";
import { StatusLine } from "./StatusLine";

const at2114 = new Date(2026, 8, 7, 21, 14);

describe("StatusLine", () => {
  it("reads the clock, the cap, the reflex and the rate while online", () => {
    render(<StatusLine overview={overviewFixture} online lastTrueAt={at2114} />);
    expect(
      screen.getByText("21:14 · cloud 1.42 / 5.00 · reflex 380 ms · 2.1 ev/s"),
    ).toBeInTheDocument();
  });

  it("stamps the last-known time and drops the rate while offline", () => {
    render(<StatusLine overview={overviewFixture} online={false} lastTrueAt={at2114} />);
    expect(screen.getByText("last true 21:14 · cloud 1.42 / 5.00 · reflex ok")).toBeInTheDocument();
  });

  it("says first run while every stream is empty", () => {
    render(<StatusLine overview={firstRunOverviewFixture} online lastTrueAt={at2114} />);
    expect(
      screen.getByText("first run · cloud 0.00 / 5.00 · reflex ok · 0 ev/s"),
    ).toBeInTheDocument();
  });

  it("says reflex ok when nothing has been measured", () => {
    render(
      <StatusLine
        overview={{ ...overviewFixture, reflex: { model: null, last_ms: null, p50_ms: null } }}
        online
        lastTrueAt={at2114}
      />,
    );
    expect(screen.getByText(/reflex ok/)).toBeInTheDocument();
  });

  it("says cloud — when there is no cost record for today", () => {
    render(<StatusLine overview={{ ...overviewFixture, cost: null }} online lastTrueAt={at2114} />);
    expect(screen.getByText(/cloud — /)).toBeInTheDocument();
  });

  it("shows an unknown clock rather than a made-up one", () => {
    render(<StatusLine overview={undefined} online={false} lastTrueAt={null} />);
    expect(screen.getByText("last true --:-- · cloud — · reflex ok")).toBeInTheDocument();
  });

  it("does not call a degraded overview a first run", () => {
    // redis down: the shape is complete but `streams` is empty. That is unknown,
    // not "nothing has ever happened".
    render(
      <StatusLine
        overview={{ ...overviewFixture, streams: {}, cost: null }}
        online
        lastTrueAt={at2114}
      />,
    );
    expect(screen.getByText("21:14 · cloud — · reflex 380 ms · 0 ev/s")).toBeInTheDocument();
  });
});
