import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TrackedAction } from "@/lib/actions";
import { hhmm } from "@/lib/format";
import { pendingActionFixture } from "@/test/fixtures";
import { DoorLayer } from "./DoorLayer";

const T0742 = Date.parse("2026-09-07T07:42:00Z");
/** 353 px track − 64 = 289 px of travel, the 393 pt phone's real geometry. */
const TRACK_WIDTH = 353;
const MAX = TRACK_WIDTH - 64;

function at(phase: TrackedAction["phase"], extra: Partial<TrackedAction> = {}): TrackedAction {
  return { action: pendingActionFixture, phase, ...extra };
}

function renderDoor(tracked: TrackedAction, options: { online?: boolean; now?: number } = {}) {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  render(
    <DoorLayer
      tracked={tracked}
      open
      online={options.online ?? true}
      now={options.now ?? T0742}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  return { onClose, onConfirm };
}

function drag(toX: number): void {
  const track = screen.getByTestId("slide-track");
  fireEvent.pointerDown(track, { clientX: 0, pointerId: 1 });
  fireEvent.pointerMove(track, { clientX: toX, pointerId: 1 });
  fireEvent.pointerUp(track, { clientX: toX, pointerId: 1 });
}

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => TRACK_WIDTH,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DoorLayer — what it says", () => {
  it("labels itself critical, with the short request id", () => {
    renderDoor(at("pending"));
    expect(screen.getByRole("dialog", { name: "Critical approval" })).toBeInTheDocument();
    expect(screen.getByText("CRITICAL · a91f")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Leave it" })).toBeInTheDocument();
  });

  it("counts the fuse down and says what it is counting to", () => {
    renderDoor(at("pending"));
    expect(screen.getByText("4:00")).toBeInTheDocument();
    expect(screen.getByText("until it lapses")).toBeInTheDocument();
    // 240 s of 300, on the Door's own ring.
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "80.0");
    expect(screen.getByTestId("fuse-ring").style.width).toBe("168px");
  });

  it("never draws more than a full ring when the phone's clock is behind", () => {
    // 360 s to a 300 s TTL's expiry: the server's clock is ahead of ours.
    renderDoor(at("pending"), { now: Date.parse("2026-09-07T07:40:00Z") });
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "100.0");
  });

  it("names the action, gives Alfred's reason, and shows the exact call", () => {
    renderDoor(at("pending"));
    expect(screen.getByRole("heading", { name: "Lock unlock" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "You asked me to let the cleaner in when she rings. She rang at 07:41.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'home.lock_unlock { entity_id: "lock.front_door", action: "unlock" }',
      ),
    ).toBeInTheDocument();
  });

  it("states the request plainly when the engine gave no reason", () => {
    renderDoor({ action: { ...pendingActionFixture, reason: null }, phase: "pending" });
    expect(
      screen.getByText("Alfred wants to run 'home.lock_unlock' on home-service."),
    ).toBeInTheDocument();
  });
});

describe("DoorLayer — pending", () => {
  it("offers the slider and says what confirming does and does not do", () => {
    renderDoor(at("pending"));
    expect(screen.getByTestId("slide-track")).toBeInTheDocument();
    expect(screen.getByText("Slide to confirm")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Approval only; the lock itself reports back separately. Releasing before the end snaps back.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back to the room" })).toBeNull();
  });

  it("confirms on a drag past 85% of the travel", () => {
    const { onConfirm } = renderDoor(at("pending"));

    drag(Math.round(MAX * 0.9));

    expect(onConfirm).toHaveBeenCalledWith("a91f3c2e");
    const knob = screen.getByTestId("slide-knob");
    expect(knob).toHaveAttribute("data-knob", String(MAX));
    // `settle` is the 600 ms overshoot curve, used exactly here and nowhere else.
    expect(knob).toHaveAttribute("data-motion", "settle");
  });

  it("snaps home without confirming when released early", () => {
    const { onConfirm } = renderDoor(at("pending"));

    drag(Math.round(MAX * 0.34));

    expect(onConfirm).not.toHaveBeenCalled();
    const knob = screen.getByTestId("slide-knob");
    expect(knob).toHaveAttribute("data-knob", "0");
    expect(knob).toHaveAttribute("data-motion", "snap");
  });

  it("follows the finger with no transition while dragging", () => {
    renderDoor(at("pending"));
    const track = screen.getByTestId("slide-track");

    fireEvent.pointerDown(track, { clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(track, { clientX: 100, pointerId: 1 });

    const knob = screen.getByTestId("slide-knob");
    expect(knob).toHaveAttribute("data-motion", "none");
    expect(knob).not.toHaveAttribute("data-knob", "0");
  });

  it("abandons the drag on pointercancel", () => {
    const { onConfirm } = renderDoor(at("pending"));
    const track = screen.getByTestId("slide-track");

    fireEvent.pointerDown(track, { clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(track, { clientX: MAX, pointerId: 1 });
    fireEvent.pointerCancel(track, { clientX: MAX, pointerId: 1 });

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByTestId("slide-knob")).toHaveAttribute("data-knob", "0");
  });

  it("refuses to confirm while offline, and says the fuse is still running", () => {
    const { onConfirm } = renderDoor(at("pending"), { online: false });

    drag(MAX);

    expect(onConfirm).not.toHaveBeenCalled();
    expect(
      screen.getByText("Cannot confirm while offline; the fuse is still running on the server."),
    ).toBeInTheDocument();
  });
});

describe("DoorLayer — after the answer", () => {
  it("says confirmed and queued, never applied", () => {
    renderDoor(at("queued", { confirmedAt: "2026-09-07T07:42:00Z" }));

    expect(screen.getByText("Confirmed · queued")).toBeInTheDocument();
    expect(
      screen.getByText("Sent to Home Assistant. Waiting for it to report (request a91f)."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("slide-track")).toBeNull();
    expect(screen.getByRole("button", { name: "Back to the room" })).toBeInTheDocument();
  });

  it("says applied only once the stream reported it, with what it reported", () => {
    renderDoor(
      at("applied", {
        confirmedAt: "2026-09-07T07:42:00Z",
        appliedAt: "2026-09-07T07:42:10Z",
        result: {
          request_id: "a91f3c2e",
          tool_name: "home.lock_unlock",
          status: "success",
        },
      }),
    );

    expect(screen.getByText("Applied")).toBeInTheDocument();
    expect(
      screen.getByText(
        `home.lock_unlock reported success at ${hhmm("2026-09-07T07:42:10Z")}.`,
      ),
    ).toBeInTheDocument();
  });

  it("says what did not happen, and how to ask again", () => {
    renderDoor(at("expired"));

    expect(screen.getByText("Expired")).toBeInTheDocument();
    expect(screen.getByText("lapsed")).toBeInTheDocument();
    expect(
      screen.getByText(
        new RegExp(
          "^The five minutes ran out at \\d{2}:\\d{2}\\. Nothing was done\\. Ask again to get a fresh one\\.$",
        ),
      ),
    ).toBeInTheDocument();
  });

  it("spells a different TTL out too", () => {
    renderDoor({ action: { ...pendingActionFixture, ttl_seconds: 600 }, phase: "expired" });
    expect(screen.getByText(/^The ten minutes ran out/)).toBeInTheDocument();
  });

  it("falls back to the number for an unusual TTL", () => {
    renderDoor({ action: { ...pendingActionFixture, ttl_seconds: 45 * 60 }, phase: "expired" });
    expect(screen.getByText(/^The 45 minutes ran out/)).toBeInTheDocument();
  });

  it("says when something else got there first", () => {
    renderDoor(at("answered"));
    expect(screen.getByText("Answered")).toBeInTheDocument();
    expect(screen.getByText("answered elsewhere")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Already answered elsewhere; the house no longer holds this request. Nothing further was sent.",
      ),
    ).toBeInTheDocument();
  });
});

describe("DoorLayer — leaving", () => {
  it("leaves it on the chevron", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDoor(at("pending"));

    await user.click(screen.getByRole("button", { name: "Leave it" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("goes back to the room once answered", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDoor(at("expired"));

    await user.click(screen.getByRole("button", { name: "Back to the room" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders nothing at all when there is no action", () => {
    render(
      <DoorLayer
        tracked={null}
        open={false}
        online
        now={T0742}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
