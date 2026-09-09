import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PresenceSignal } from "@/lib/presence-signal";
import { HoldToTalk } from "./HoldToTalk";

const { recorders, state } = vi.hoisted(() => ({
  recorders: [] as { start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }[],
  state: { durationMs: 2400, startRejects: false },
}));

const FAKE_ANALYSER = { fftSize: 256 } as unknown as AnalyserNode;

vi.mock("@/lib/recorder", () => {
  class Recorder {
    analyser: AnalyserNode | null = FAKE_ANALYSER;
    start = vi.fn(async () => {
      if (state.startRejects) throw new Error("NotAllowedError");
    });
    stop = vi.fn(async () => ({
      blob: new Blob(["audio"], { type: "audio/mp4" }),
      mimeType: "audio/mp4",
      durationMs: state.durationMs,
    }));
    constructor() {
      recorders.push(this);
    }
  }
  return {
    Recorder,
    blobToDataUrl: async () => "data:audio/mp4;base64,AAAA",
    pickMimeType: () => "audio/mp4",
  };
});

function renderHold(online = true) {
  const signal = new PresenceSignal();
  const setHolding = vi.spyOn(signal, "setHolding");
  const attach = vi.spyOn(signal, "attach");
  const onAudio = vi.fn();
  const onHoldingChange = vi.fn();
  render(
    <div style={{ position: "relative" }}>
      <HoldToTalk
        signal={signal}
        online={online}
        onAudio={onAudio}
        onHoldingChange={onHoldingChange}
      />
    </div>,
  );
  return {
    button: screen.getByRole("button", { name: "Hold to talk" }),
    setHolding,
    attach,
    onAudio,
    onHoldingChange,
  };
}

async function press(button: HTMLElement): Promise<void> {
  await act(async () => {
    fireEvent.pointerDown(button, { pointerId: 1 });
  });
}

async function release(button: HTMLElement): Promise<void> {
  await act(async () => {
    fireEvent.pointerUp(button, { pointerId: 1 });
  });
}

beforeEach(() => {
  recorders.length = 0;
  state.durationMs = 2400;
  state.startRejects = false;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("HoldToTalk", () => {
  it("is a 56 px button, and refuses to record while offline", () => {
    const { button } = renderHold(false);
    expect(button).toHaveClass("h-14", "w-14");
    expect(button).toBeDisabled();
  });

  it("starts recording, drives the presence field and shows the caption", async () => {
    const { button, setHolding, attach, onHoldingChange } = renderHold();

    await press(button);

    expect(recorders).toHaveLength(1);
    expect(recorders[0].start).toHaveBeenCalled();
    expect(setHolding).toHaveBeenCalledWith(true);
    expect(attach).toHaveBeenCalledWith(FAKE_ANALYSER);
    expect(onHoldingChange).toHaveBeenCalledWith(true);
    expect(screen.getByText("recording 0:00 · release to send")).toBeInTheDocument();
  });

  it("counts the seconds while it is held", async () => {
    vi.useFakeTimers();
    const { button } = renderHold();
    await press(button);

    act(() => void vi.advanceTimersByTime(4000));

    expect(screen.getByText("recording 0:04 · release to send")).toBeInTheDocument();
  });

  it("sends the recording and its length on release", async () => {
    const { button, onAudio, setHolding, attach, onHoldingChange } = renderHold();
    await press(button);

    await release(button);

    await waitFor(() =>
      expect(onAudio).toHaveBeenCalledWith("data:audio/mp4;base64,AAAA", 2.4),
    );
    expect(recorders[0].stop).toHaveBeenCalled();
    expect(setHolding).toHaveBeenLastCalledWith(false);
    expect(attach).toHaveBeenLastCalledWith(null);
    expect(onHoldingChange).toHaveBeenLastCalledWith(false);
    expect(screen.queryByText(/release to send/)).toBeNull();
  });

  it("throws away anything under a second", async () => {
    state.durationMs = 640;
    const { button, onAudio } = renderHold();

    await press(button);
    await release(button);

    expect(onAudio).not.toHaveBeenCalled();
    // The microphone is still released — a discarded take must not leak the stream.
    expect(recorders[0].stop).toHaveBeenCalled();
  });

  it("discards on pointercancel", async () => {
    const { button, onAudio, setHolding } = renderHold();
    await press(button);

    await act(async () => {
      fireEvent.pointerCancel(button, { pointerId: 1 });
    });

    expect(onAudio).not.toHaveBeenCalled();
    expect(setHolding).toHaveBeenLastCalledWith(false);
  });

  it("captures the pointer so a finger sliding off still ends the take", async () => {
    const capture = vi.spyOn(Element.prototype, "setPointerCapture");
    const { button } = renderHold();

    await press(button);

    expect(capture).toHaveBeenCalledWith(1);
  });

  it("ignores a second press while already recording", async () => {
    const { button } = renderHold();
    await press(button);
    await press(button);
    expect(recorders).toHaveLength(1);
  });

  it("leaves the room as it was when the microphone is refused", async () => {
    state.startRejects = true;
    const { button, onAudio, setHolding, onHoldingChange } = renderHold();

    await press(button);

    expect(onAudio).not.toHaveBeenCalled();
    expect(setHolding).toHaveBeenLastCalledWith(false);
    expect(onHoldingChange).toHaveBeenLastCalledWith(false);
    expect(screen.queryByText(/release to send/)).toBeNull();
  });
});
