import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TrackedAction } from "@/lib/actions";
import { hhmm } from "@/lib/format";
import { hintOpacity, slideKnob } from "@/lib/slide";
import { pendingActionFixture } from "@/test/fixtures";
import { DoorLayer, type DoorLayerProps } from "./DoorLayer";

const T0742 = Date.parse("2026-09-07T07:42:00Z");
const T0745_29 = Date.parse("2026-09-07T07:45:29Z");
const T0745_30 = Date.parse("2026-09-07T07:45:30Z");
/** 353 px track − 64 = 289 px of travel, the 393 pt phone's real geometry. */
const TRACK_WIDTH = 353;
const MAX = TRACK_WIDTH - 64;

function at(phase: TrackedAction["phase"], extra: Partial<TrackedAction> = {}): TrackedAction {
  return { action: pendingActionFixture, phase, ...extra };
}

function renderDoor(tracked: TrackedAction, options: { online?: boolean; now?: number } = {}) {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  const props: DoorLayerProps = {
    tracked,
    open: true,
    online: options.online ?? true,
    now: options.now ?? T0742,
    onClose,
    onConfirm,
  };
  const view = render(<DoorLayer {...props} />);
  return {
    onClose,
    onConfirm,
    rerender: (next: Partial<DoorLayerProps>) => view.rerender(<DoorLayer {...props} {...next} />),
  };
}

function drag(toX: number): void {
  const track = screen.getByTestId("slide-track");
  fireEvent.pointerDown(track, { clientX: 0, pointerId: 1 });
  fireEvent.pointerMove(track, { clientX: toX, pointerId: 1 });
  fireEvent.pointerUp(track, { clientX: toX, pointerId: 1 });
}

function slider(): HTMLElement {
  return screen.getByRole("slider", { name: "Slide to confirm" });
}

function press(key: string, times = 1): void {
  for (let i = 0; i < times; i += 1) fireEvent.keyDown(slider(), { key });
}

function fuseColor(): string {
  return screen.getByTestId("fuse-arc").style.getPropertyValue("--fuse-color");
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
    // 360 s to a 300 s fuse's expiry: the server's clock is ahead of ours.
    renderDoor(at("pending"), { now: Date.parse("2026-09-07T07:40:00Z") });
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "100.0");
  });

  it("measures the ring against the whole fuse, not what was left when it was read", () => {
    // Read again three minutes in — the server says 120 s left. Still 240 of 300:
    // the ring must not snap back to full every time the app comes to the front.
    renderDoor({ action: { ...pendingActionFixture, ttl_seconds: 120 }, phase: "pending" });
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "80.0");
  });

  it("turns the ring to paper at thirty seconds, and not a second before", () => {
    const { rerender } = renderDoor(at("pending"), { now: T0745_29 });
    expect(fuseColor()).toBe("var(--accent)");
    rerender({ now: T0745_30 });
    expect(fuseColor()).toBe("var(--paper)");
  });

  it("keeps the slider clear of the home indicator", () => {
    renderDoor(at("pending"));
    // jsdom rewrites the calc(); what matters is that the inset is in it.
    const foot = slider().parentElement as HTMLElement;
    expect(foot.style.paddingBottom).toContain("40px");
    expect(foot.style.paddingBottom).toContain("safe-area-inset-bottom");
    expect(foot).not.toHaveClass("pb-10");
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
    // Drawn where the bookkeeping says: landed on the end, not just recorded there.
    expect(knob.style.transform).toBe(`translateX(${MAX}px)`);
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
    // The eased position (pinned in slide.test.ts) is what is drawn, and the
    // hint fades with it rather than sitting under the knob.
    const eased = slideKnob(100, MAX);
    expect(knob).toHaveAttribute("data-knob", String(Math.round(eased)));
    expect(knob.style.transform).toBe(`translateX(${eased}px)`);
    expect(screen.getByText("Slide to confirm").style.opacity).toBe(String(hintOpacity(eased, MAX)));
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

describe("DoorLayer — the slider without a finger", () => {
  it("is a slider by name, at nought, and in the tab order", () => {
    renderDoor(at("pending"));
    const track = slider();
    expect(track).toHaveAttribute("aria-valuemin", "0");
    expect(track).toHaveAttribute("aria-valuemax", "100");
    expect(track).toHaveAttribute("aria-valuenow", "0");
    expect(track).toHaveAttribute("aria-disabled", "false");
    expect(track).toHaveAttribute("tabindex", "0");
    // The hint is the slider's name already; read once, not twice.
    expect(screen.getByText("Slide to confirm")).toHaveAttribute("aria-hidden", "true");
  });

  it("walks a tenth at a time, and confirms on the tenth step and no earlier", () => {
    const { onConfirm } = renderDoor(at("pending"));

    press("ArrowRight", 9);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(slider()).toHaveAttribute("aria-valuenow", "90");
    expect(screen.getByTestId("slide-knob")).toHaveAttribute(
      "data-knob",
      String(Math.round(MAX * 0.9)),
    );

    press("ArrowRight");
    expect(onConfirm).toHaveBeenCalledWith("a91f3c2e");
    const knob = screen.getByTestId("slide-knob");
    expect(slider()).toHaveAttribute("aria-valuenow", "100");
    expect(knob.style.transform).toBe(`translateX(${MAX}px)`);
    expect(knob).toHaveAttribute("data-motion", "settle");
  });

  it("steps back, and home, without confirming", () => {
    const { onConfirm } = renderDoor(at("pending"));

    press("ArrowUp", 3);
    expect(slider()).toHaveAttribute("aria-valuenow", "30");
    press("ArrowLeft");
    expect(slider()).toHaveAttribute("aria-valuenow", "20");
    press("ArrowDown", 5);
    expect(slider()).toHaveAttribute("aria-valuenow", "0");
    press("ArrowRight", 4);
    press("Home");
    expect(slider()).toHaveAttribute("aria-valuenow", "0");

    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("confirms once on End, and not on Enter or Space", () => {
    const { onConfirm } = renderDoor(at("pending"));

    expect(fireEvent.keyDown(slider(), { key: "Enter" })).toBe(true);
    expect(fireEvent.keyDown(slider(), { key: " " })).toBe(true);
    expect(onConfirm).not.toHaveBeenCalled();

    // A key it takes is a key the page must not also scroll on.
    expect(fireEvent.keyDown(slider(), { key: "End" })).toBe(false);
    press("End");
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(slider()).toHaveAttribute("aria-valuenow", "100");
  });

  it("is out of reach while offline", () => {
    const { onConfirm } = renderDoor(at("pending"), { online: false });

    const track = slider();
    expect(track).toHaveAttribute("aria-disabled", "true");
    expect(track).toHaveAttribute("tabindex", "-1");
    press("End");
    expect(onConfirm).not.toHaveBeenCalled();
    expect(track).toHaveAttribute("aria-valuenow", "0");
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

  it("says less, not more, when applied arrives without its facts", () => {
    renderDoor(at("applied", { confirmedAt: "2026-09-07T07:42:00Z" }));
    expect(screen.getByText("home.lock_unlock reported back.")).toBeInTheDocument();
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

  it("names the fuse's whole length, not what was left when it was read", () => {
    // Read twenty seconds before it lapsed: the server said 20, the fuse was 300.
    renderDoor({ action: { ...pendingActionFixture, ttl_seconds: 20 }, phase: "expired" });
    expect(screen.getByText(/^The five minutes ran out/)).toBeInTheDocument();
  });

  it("rounds to the minute the server meant, since a read shaves a fraction off", () => {
    // Redis reports whole seconds and the payload adds them to "now": the two
    // clocks are 299.4 s apart, not 300, and that is still five minutes.
    renderDoor({
      action: { ...pendingActionFixture, expires_at: "2026-09-07T07:45:59.400Z" },
      phase: "expired",
    });
    expect(screen.getByText(/^The five minutes ran out/)).toBeInTheDocument();
  });

  it("spells a different fuse out too", () => {
    renderDoor({
      action: { ...pendingActionFixture, expires_at: "2026-09-07T07:51:00Z" },
      phase: "expired",
    });
    expect(screen.getByText(/^The ten minutes ran out/)).toBeInTheDocument();
  });

  it("falls back to the number for an unusual fuse", () => {
    renderDoor({
      action: { ...pendingActionFixture, expires_at: "2026-09-07T08:26:00Z" },
      phase: "expired",
    });
    expect(screen.getByText(/^The 45 minutes ran out/)).toBeInTheDocument();
  });

  it("has a singular for one minute, and no number at all under half of one", () => {
    const { rerender } = renderDoor({
      action: { ...pendingActionFixture, expires_at: "2026-09-07T07:42:00Z" },
      phase: "expired",
    });
    expect(screen.getByText(/^The one minute ran out/)).toBeInTheDocument();
    rerender({
      tracked: { action: { ...pendingActionFixture, expires_at: "2026-09-07T07:41:20Z" }, phase: "expired" },
    });
    expect(screen.getByText(/^The time ran out/)).toBeInTheDocument();
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

  it("keeps the last action on the panel while it slides away", () => {
    const { rerender } = renderDoor(at("queued", { confirmedAt: "2026-09-07T07:42:00Z" }));
    // `close()` clears the action at once; the copy must outlive it by the leave.
    rerender({ tracked: null, open: false });
    expect(screen.getByRole("heading", { name: "Lock unlock" })).toBeInTheDocument();
    expect(screen.getByText("Confirmed · queued")).toBeInTheDocument();
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
