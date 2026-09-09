import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { CONFIRM_RATIO, hintOpacity, slideKnob } from "@/lib/slide";

/** Knob 56 px at a 4 px inset — the travel is the track minus both. */
const KNOB_INSET = 64;
/** The one place the handoff's `settle` curve is used: when the knob lands. */
const SETTLE_MS = 600;
const SNAP_MS = 380;
/** Arrow keys (and VoiceOver's adjust gesture) move a tenth of the travel. */
const KEY_STEPS = 10;

export interface SlideToConfirmProps {
  hint: string;
  disabled: boolean;
  onConfirm: () => void;
}

export function SlideToConfirm({ hint, disabled, onConfirm }: SlideToConfirmProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const startXRef = useRef(0);
  const [max, setMax] = useState(0);
  const [knob, setKnob] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [settling, setSettling] = useState(false);

  // Returns the travel as well as storing it: the keyboard path needs it in
  // the same event, before the state has come round.
  function measure(): number {
    const width = trackRef.current?.clientWidth ?? 0;
    const travel = Math.max(0, width - KNOB_INSET);
    setMax(travel);
    return travel;
  }

  // Measure once so the hint's opacity is right before anything is touched; the
  // width is measured again on every press, because the keyboard and rotation
  // both change it.
  useEffect(() => {
    measure();
  }, []);

  function down(event: ReactPointerEvent<HTMLDivElement>): void {
    if (disabled) return;
    measure();
    startXRef.current = event.clientX;
    trackRef.current?.setPointerCapture(event.pointerId);
    setSettling(false);
    setDragging(true);
    setKnob(0);
  }

  function move(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!dragging) return;
    setKnob(slideKnob(event.clientX - startXRef.current, max));
  }

  function up(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!dragging) return;
    trackRef.current?.releasePointerCapture(event.pointerId);
    setDragging(false);

    const travelled = slideKnob(event.clientX - startXRef.current, max);
    if (max > 0 && travelled >= max * CONFIRM_RATIO) {
      setSettling(true);
      setKnob(max);
      onConfirm();
      return;
    }
    setKnob(0);
  }

  function cancel(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!dragging) return;
    trackRef.current?.releasePointerCapture(event.pointerId);
    setDragging(false);
    setKnob(0);
  }

  // The non-pointer road. The knob walks in tenths and confirms only on the
  // last one: no single key, and no single VoiceOver flick, is ever a yes. The
  // step is counted from where the knob is, so ten presses land exactly on
  // the end instead of a float short of it.
  function key(event: ReactKeyboardEvent<HTMLDivElement>): void {
    if (disabled || dragging || settling) return;
    const travel = measure();
    if (travel <= 0) return;
    const step = Math.round((knob / travel) * KEY_STEPS);
    let next: number;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowUp":
        next = Math.min(KEY_STEPS, step + 1);
        break;
      case "ArrowLeft":
      case "ArrowDown":
        next = Math.max(0, step - 1);
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = KEY_STEPS;
        break;
      default:
        return;
    }
    event.preventDefault();
    setKnob((next / KEY_STEPS) * travel);
    if (next === KEY_STEPS) {
      setSettling(true);
      onConfirm();
    }
  }

  return (
    <div
      ref={trackRef}
      data-testid="slide-track"
      role="slider"
      aria-label={hint}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={max > 0 ? Math.round((knob / max) * 100) : 0}
      aria-disabled={disabled}
      tabIndex={disabled ? -1 : 0}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
      onKeyDown={key}
      className="relative h-16 overflow-hidden rounded-[32px]"
      style={{
        background: "var(--ring)",
        // Without this, iOS treats the drag as a page scroll and the knob never moves.
        touchAction: "none",
        userSelect: "none",
        WebkitUserSelect: "none",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <div
        aria-hidden="true"
        className="absolute inset-0 flex items-center justify-center pl-14 text-[15px]"
        style={{
          color: "var(--paper-muted)",
          opacity: hintOpacity(knob, max),
          transition: "opacity .15s",
        }}
      >
        {hint}
      </div>

      <div
        data-testid="slide-knob"
        data-knob={String(Math.round(knob))}
        data-motion={dragging ? "none" : settling ? "settle" : "snap"}
        className="absolute top-1 left-1 flex h-14 w-14 items-center justify-center rounded-[28px]"
        style={{
          background: "var(--accent)",
          transform: `translateX(${knob}px)`,
          transition: dragging
            ? "none"
            : settling
              ? `transform ${SETTLE_MS}ms var(--ease-settle)`
              : `transform ${SNAP_MS}ms var(--ease-rise)`,
        }}
      >
        <span
          aria-hidden="true"
          className="block h-2.5 w-2.5 border-t-2 border-r-2"
          style={{ borderColor: "var(--ink)", transform: "rotate(45deg)" }}
        />
      </div>
    </div>
  );
}
