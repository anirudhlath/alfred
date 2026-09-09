import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { mmss } from "@/lib/format";
import type { PresenceSignal } from "@/lib/presence-signal";
import { blobToDataUrl, Recorder } from "@/lib/recorder";

/** Under this, it was a mis-tap and not a sentence. Handoff: "<1 s releases do nothing". */
const MIN_HOLD_MS = 1000;
/** 3 px bars, staggered exactly as the handoff's `wave` keyframe describes. */
const BAR_DELAYS = [0, 0.15, 0.3, 0.1];

export interface HoldToTalkProps {
  signal: PresenceSignal;
  online: boolean;
  /** Lifts `holding` to the Room, which is where the headline reads it. */
  onHoldingChange: (holding: boolean) => void;
  onAudio: (dataUrl: string, seconds: number) => void;
}

export function HoldToTalk({ signal, online, onHoldingChange, onAudio }: HoldToTalkProps) {
  const [holding, setHolding] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const recorderRef = useRef<Recorder | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopTicking(): void {
    if (!tickRef.current) return;
    clearInterval(tickRef.current);
    tickRef.current = null;
  }

  async function finish(send: boolean): Promise<void> {
    const recorder = recorderRef.current;
    recorderRef.current = null;

    stopTicking();
    setHolding(false);
    setSeconds(0);
    onHoldingChange(false);
    signal.setHolding(false);
    signal.attach(null);

    if (!recorder) return;

    // Always stop, whether or not we are sending: the microphone indicator stays
    // on until the tracks are released.
    const recording = await recorder.stop();
    if (!send || !recording) return;
    if (recording.durationMs < MIN_HOLD_MS) return;

    const dataUrl = await blobToDataUrl(recording.blob);
    onAudio(dataUrl, recording.durationMs / 1000);
  }

  async function begin(event: ReactPointerEvent<HTMLButtonElement>): Promise<void> {
    if (!online || recorderRef.current) return;
    // Capture first: a finger that slides off a 56 px button mid-sentence would
    // otherwise never deliver pointerup here, and the take would run for ever.
    event.currentTarget.setPointerCapture(event.pointerId);

    const recorder = new Recorder();
    recorderRef.current = recorder;
    setHolding(true);
    onHoldingChange(true);
    signal.setHolding(true);
    tickRef.current = setInterval(() => setSeconds((current) => current + 1), 1000);

    try {
      await recorder.start();
    } catch {
      // Permission refused, or no microphone. Unwind quietly — the composer is
      // still there and the user can type. Only if this take is still the live
      // one: a rejection that lands after the hold ended must not tear down
      // whatever replaced it.
      if (recorderRef.current === recorder) await finish(false);
      return;
    }
    // The hold can end — or this button unmount — while getUserMedia is still
    // prompting. `finish()` ran when there was nothing to stop yet, so release
    // what `start()` has only now opened instead of attaching an analyser to a
    // take nobody is holding: otherwise the microphone runs until the tab closes
    // and iOS leaves its indicator on.
    if (recorderRef.current !== recorder) {
      void recorder.stop();
      return;
    }
    signal.attach(recorder.analyser);
  }

  // The Room outlives this button — Composer swaps the slot for Send the moment a
  // draft appears — so unmounting mid-hold must hand the presence field and the
  // headline back, not only close the microphone. Read through a ref so the
  // cleanup stays an unmount-only `[]` effect: a dependency on `onHoldingChange`
  // would re-run it, and kill the take, on every parent render.
  const latest = useRef({ signal, onHoldingChange });
  useEffect(() => {
    latest.current = { signal, onHoldingChange };
  });

  useEffect(() => {
    return () => {
      stopTicking();
      const recorder = recorderRef.current;
      recorderRef.current = null;
      if (!recorder) return;
      void recorder.stop();
      latest.current.signal.setHolding(false);
      latest.current.signal.attach(null);
      latest.current.onHoldingChange(false);
    };
  }, []);

  return (
    <>
      <button
        type="button"
        aria-label="Hold to talk"
        disabled={!online}
        onPointerDown={(event) => void begin(event)}
        onPointerUp={() => void finish(true)}
        onPointerCancel={() => void finish(false)}
        className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[28px] border-[1.5px] transition-transform duration-150"
        style={{
          borderColor: "var(--accent)",
          background: holding ? "var(--accent)" : "transparent",
          transform: holding ? "scale(1.12)" : "scale(1)",
          touchAction: "none",
          userSelect: "none",
          WebkitUserSelect: "none",
        }}
      >
        {holding ? (
          <span aria-hidden="true" className="flex h-[22px] items-center gap-[3px]">
            {BAR_DELAYS.map((delay) => (
              <span
                key={delay}
                className="h-[22px] w-[3px] rounded-[2px]"
                style={{
                  background: "var(--ink)",
                  animation: `wave .7s ${delay}s ease-in-out infinite`,
                }}
              />
            ))}
          </span>
        ) : (
          <span
            aria-hidden="true"
            className="block h-[22px] w-3.5 rounded-[7px]"
            style={{ background: online ? "var(--accent)" : "var(--muted)" }}
          />
        )}
      </button>

      {holding ? (
        <div className="t-meta pointer-events-none absolute inset-x-0 bottom-[84px] z-[1] text-center">
          recording {mmss(seconds)} · release to send
        </div>
      ) : null}
    </>
  );
}
