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
    setSeconds(0);
    onHoldingChange(true);
    signal.setHolding(true);
    tickRef.current = setInterval(() => setSeconds((current) => current + 1), 1000);

    try {
      await recorder.start();
    } catch {
      // Permission refused, or no microphone. Unwind quietly — the composer is
      // still there and the user can type.
      await finish(false);
      return;
    }
    signal.attach(recorder.analyser);
  }

  useEffect(() => {
    return () => {
      stopTicking();
      void recorderRef.current?.stop();
      recorderRef.current = null;
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
