import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PresenceSignal } from "@/lib/presence-signal";
import { HoldToTalk } from "./HoldToTalk";

const { recorders, state } = vi.hoisted(() => ({
  recorders: [] as {
    start: ReturnType<typeof vi.fn>;
    stop: ReturnType<typeof vi.fn>;
    live: boolean;
  }[],
  state: { durationMs: 2400, startRejects: false, startGate: null as Promise<void> | null },
}));

const FAKE_ANALYSER = { fftSize: 256 } as unknown as AnalyserNode;

vi.mock("@/lib/recorder", () => {
  class Recorder {
    analyser: AnalyserNode | null = FAKE_ANALYSER;
    /** True between a resolved `start()` and `stop()` — the microphone is open. */
    live = false;
    start = vi.fn(async () => {
      // `startGate` stands in for the permission prompt: until it resolves,
      // getUserMedia has not returned and there is nothing to stop. Both knobs
      // are read at call time, so a later take can be configured differently
      // while an earlier prompt is still open.
      const gate = state.startGate;
      const rejects = state.startRejects;
      if (gate) await gate;
      if (rejects) throw new Error("NotAllowedError");
      this.live = true;
    });
    stop = vi.fn(async () => {
      if (!this.live) return null;
      this.live = false;
      return {
        blob: new Blob(["audio"], { type: "audio/mp4" }),
        mimeType: "audio/mp4",
        durationMs: state.durationMs,
      };
    });
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
  const view = render(
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
    unmount: view.unmount,
    rerender: view.rerender,
    signal,
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
  state.startGate = null;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("HoldToTalk", () => {
  it("is a 56 px button, and refuses to record while offline", async () => {
    const { button } = renderHold(false);
    expect(button).toHaveClass("h-14", "w-14");
    expect(button).toBeDisabled();

    // `disabled` is not the whole guard: React still runs onPointerDown for a
    // disabled button, and WebKit dispatches pointer events over one too.
    await press(button);
    expect(recorders).toHaveLength(0);
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
    // Four bars, as the handoff draws them.
    expect(button.querySelectorAll('span[style*="wave"]')).toHaveLength(4);
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

  it("discards on pointercancel, and still releases the microphone", async () => {
    const { button, onAudio, setHolding } = renderHold();
    await press(button);

    await act(async () => {
      fireEvent.pointerCancel(button, { pointerId: 1 });
    });

    expect(onAudio).not.toHaveBeenCalled();
    expect(setHolding).toHaveBeenLastCalledWith(false);
    // A call or a system sheet steals the pointer mid-sentence; the microphone
    // indicator must go out even though nothing is sent.
    expect(recorders[0].stop).toHaveBeenCalled();
    expect(recorders[0].live).toBe(false);
  });

  it("keeps a take that is exactly a second", async () => {
    state.durationMs = 1000;
    const { button, onAudio } = renderHold();

    await press(button);
    await release(button);

    await waitFor(() => expect(onAudio).toHaveBeenCalledWith("data:audio/mp4;base64,AAAA", 1));
  });

  it("stops counting once a take is released", async () => {
    vi.useFakeTimers();
    const { button } = renderHold();
    await press(button);
    act(() => void vi.advanceTimersByTime(3000));

    await release(button);
    act(() => void vi.advanceTimersByTime(3000));
    await press(button);

    expect(screen.getByText("recording 0:00 · release to send")).toBeInTheDocument();
  });

  it("releases the microphone when the hold ends before the prompt is answered", async () => {
    let answer = (): void => {};
    state.startGate = new Promise<void>((resolve) => {
      answer = resolve;
    });
    const { button, attach, onAudio } = renderHold();

    await press(button);
    await release(button);

    // Only now does the user tap Allow and getUserMedia resolve.
    await act(async () => {
      answer();
      await state.startGate;
    });

    expect(recorders[0].live).toBe(false);
    expect(attach).toHaveBeenLastCalledWith(null);
    expect(onAudio).not.toHaveBeenCalled();
  });

  it("leaves the next take alone when an earlier refusal finally lands", async () => {
    let refuse = (): void => {};
    state.startGate = new Promise<void>((resolve) => {
      refuse = resolve;
    });
    state.startRejects = true;
    const { button, setHolding, onHoldingChange } = renderHold();

    await press(button);
    await release(button);

    // A second take is already running by the time the first prompt is answered.
    state.startGate = null;
    state.startRejects = false;
    await press(button);

    await act(async () => {
      refuse();
      await Promise.resolve();
    });

    expect(recorders).toHaveLength(2);
    expect(recorders[1].live).toBe(true);
    expect(setHolding).toHaveBeenLastCalledWith(true);
    expect(onHoldingChange).toHaveBeenLastCalledWith(true);
    expect(screen.getByText(/release to send/)).toBeInTheDocument();
  });

  it("closes the microphone and hands the field back when unmounted mid-take", async () => {
    const { button, unmount, setHolding, attach, onHoldingChange } = renderHold();
    await press(button);

    await act(async () => {
      unmount();
    });

    expect(recorders[0].live).toBe(false);
    expect(setHolding).toHaveBeenLastCalledWith(false);
    expect(attach).toHaveBeenLastCalledWith(null);
    expect(onHoldingChange).toHaveBeenLastCalledWith(false);
  });

  it("hands the field back through the callback it was last given", async () => {
    const { button, unmount, rerender, signal, onAudio, onHoldingChange } = renderHold();
    await press(button);

    // The Room re-renders while the finger is down; a cleanup that captured the
    // mount-time callback would report to a prop nobody reads any more.
    const replacement = vi.fn();
    rerender(
      <div style={{ position: "relative" }}>
        <HoldToTalk signal={signal} online onAudio={onAudio} onHoldingChange={replacement} />
      </div>,
    );
    await act(async () => {
      unmount();
    });

    expect(replacement).toHaveBeenLastCalledWith(false);
    expect(onHoldingChange).toHaveBeenLastCalledWith(true);
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
