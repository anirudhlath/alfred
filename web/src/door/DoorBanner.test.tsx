import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { TrackedAction } from "@/lib/actions";
import { pendingActionFixture } from "@/test/fixtures";
import { DoorBanner } from "./DoorBanner";
import { FuseRing } from "./FuseRing";

const T0740 = Date.parse("2026-09-07T07:40:00Z");
const T0742 = Date.parse("2026-09-07T07:42:00Z");
const T0745_29 = Date.parse("2026-09-07T07:45:29Z");
const T0745_30 = Date.parse("2026-09-07T07:45:30Z");
const T0747 = Date.parse("2026-09-07T07:47:00Z");

const tracked: TrackedAction = { action: pendingActionFixture, phase: "pending" };

describe("FuseRing", () => {
  it("hands the arc its fraction and its colour as custom properties", () => {
    render(<FuseRing percent={80} size={168} danger={false} />);
    const arc = screen.getByTestId("fuse-arc");
    // Custom properties, not an inline `background`: jsdom's CSS parser drops a
    // conic-gradient shorthand it cannot parse, and the assertion would be
    // meaningless. The gradient itself lives in index.css.
    expect(arc.style.getPropertyValue("--fuse-pct")).toBe("80.0%");
    expect(arc.style.getPropertyValue("--fuse-color")).toBe("var(--accent)");
    expect(arc).toHaveClass("fuse-arc");
  });

  it("turns to paper in the last thirty seconds", () => {
    render(<FuseRing percent={9} size={168} danger />);
    expect(screen.getByTestId("fuse-arc").style.getPropertyValue("--fuse-color")).toBe(
      "var(--paper)",
    );
  });

  it("masks each size with that size's own class", () => {
    const { rerender } = render(<FuseRing percent={50} size={168} danger={false} />);
    expect(screen.getByTestId("fuse-arc")).toHaveClass("fuse-168");

    rerender(<FuseRing percent={50} size={34} danger={false} />);
    expect(screen.getByTestId("fuse-arc")).toHaveClass("fuse-34");
  });

  it("is drawn at the size it was asked for", () => {
    render(<FuseRing percent={50} size={34} danger={false} />);
    const ring = screen.getByTestId("fuse-ring");
    expect(ring.style.width).toBe("34px");
    expect(ring.style.height).toBe("34px");
  });

  it("puts its children in the middle of the ring", () => {
    render(
      <FuseRing percent={50} size={168} danger={false}>
        <span>4:12</span>
      </FuseRing>,
    );
    expect(screen.getByText("4:12")).toBeInTheDocument();
    expect(screen.getByTestId("fuse-ring")).toHaveClass("items-center", "justify-center");
  });
});

describe("DoorBanner", () => {
  it("names the action, counts it down, and says who it is for", () => {
    render(<DoorBanner tracked={tracked} now={T0742} onOpen={() => {}} />);

    expect(screen.getByText("Lock unlock")).toBeInTheDocument();
    expect(screen.getByText("expires in 4:00 · asked by Alfred, for you")).toBeInTheDocument();
    expect(screen.getByText("Open")).toBeInTheDocument();
    // The banner's ring, at the banner's size, in the unhurried colour.
    expect(screen.getByTestId("fuse-ring").style.width).toBe("34px");
    expect(screen.getByTestId("fuse-arc").style.getPropertyValue("--fuse-color")).toBe(
      "var(--accent)",
    );
  });

  it("draws the arc as the fraction of the server's own TTL", () => {
    render(<DoorBanner tracked={tracked} now={T0742} onOpen={() => {}} />);
    // 240 s left of 300.
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "80.0");
  });

  it("goes to paper at thirty seconds, and not a second before", () => {
    const { rerender } = render(<DoorBanner tracked={tracked} now={T0745_29} onOpen={() => {}} />);
    const colour = () => screen.getByTestId("fuse-arc").style.getPropertyValue("--fuse-color");
    expect(colour()).toBe("var(--accent)");

    rerender(<DoorBanner tracked={tracked} now={T0745_30} onOpen={() => {}} />);
    expect(colour()).toBe("var(--paper)");
  });

  it("never draws more than a full ring when the phone's clock is behind", () => {
    // 360 s to a 300 s TTL's expiry: the server's clock is ahead of ours.
    render(<DoorBanner tracked={tracked} now={T0740} onOpen={() => {}} />);
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "100.0");
  });

  it("reads 0:00 rather than a negative fuse", () => {
    render(<DoorBanner tracked={tracked} now={T0747} onOpen={() => {}} />);
    expect(screen.getByText("expires in 0:00 · asked by Alfred, for you")).toBeInTheDocument();
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "0.0");
  });

  it("is one tap target, and opens the Door", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<DoorBanner tracked={tracked} now={T0742} onOpen={onOpen} />);

    await user.click(screen.getByRole("button", { name: /Lock unlock/ }));

    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("survives a zero TTL without dividing by it", () => {
    render(
      <DoorBanner
        tracked={{ action: { ...pendingActionFixture, ttl_seconds: 0 }, phase: "pending" }}
        now={T0742}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "0.0");
  });
});
