import type { ReactNode } from "react";
import {
  RING_COUNT_ON_OPACITY,
  ringFill,
  ringText,
  STREAM_INFO,
  STREAMS,
  type StreamName,
  streamLabel,
} from "@/lib/streams";

export interface StreamChipsProps {
  counts: Record<StreamName, number>;
  solo: StreamName | null;
  onSolo: (name: StreamName | null) => void;
  /** The desktop's chips, in one row with the bench's controls (`Alfred Desktop.dc.html:53-64`). */
  compact?: boolean;
  /** What follows the eight in the compact row. The phone's footer has its own row for these. */
  trailing?: ReactNode;
}

/**
 * Eight equal chips, monogram over count (handoff, Activity footer). Tapping
 * one solos its stream and tapping it again clears. The count is what the
 * bench holds, not what the server has.
 *
 * More precisely it is loaded depth per stream, which is why a count can read
 * higher than the rows on screen: with all eight shown the list stops at the
 * shared horizon, so a stream `↑ older` paged deeper than the rest holds
 * entries the merged view is not yet allowed to show. Solo it to see them all,
 * on the phone: at width a solo moves no horizon, and `↑ older` is the way down.
 *
 * The other seven recede by losing their hue, not by going translucent: the
 * count is the chip's only data, and 35 % opacity took it to 1.31:1 in light
 * theme. The label lands on `--fg2` (8.28:1) rather than `--muted` (3.46:1,
 * under AA for 10 px text), and the border on `--muted` — 3.46:1 clears the
 * 3:1 a UI component needs, where `--line` would leave seven adjacent tap
 * targets at 1.18:1 with no edge between them.
 *
 * `compact` keeps every hue, as the mock draws it: the receding is for the
 * phone's stacked 44 px chip (R19). Its count is beside the monogram, at a touch
 * less than full strength, though not the mock's .7 and .8: those are 2.77:1 and
 * 3.85:1 at their worst, and this is still the chip's only data, so each stops
 * where AA does (`--ring-count-opacity`, `RING_COUNT_ON_OPACITY`). Real counts run
 * to 400 and the mock has single digits, so a narrow chip drops its count under its
 * monogram and the row drops `trailing` to a second line, under 352 px: 240 for the
 * eight, the gap, and the 106 of `All` and `Pause` (R20).
 */
export function StreamChips({ counts, solo, onSolo, compact = false, trailing }: StreamChipsProps) {
  const chips = STREAMS.map((name) => {
    const { mono, hue } = STREAM_INFO[name];
    const on = solo === name;
    const dimmed = !compact && solo !== null && !on;
    const ink = ringText(hue);
    return (
      <button
        key={name}
        type="button"
        // Monogram first, so the visible label opens the spoken one (WCAG
        // 2.5.3); then the words, because nothing on screen says what HS is.
        aria-label={`${mono} ${streamLabel(name)}, ${counts[name]}`}
        aria-pressed={on}
        onClick={() => onSolo(on ? null : name)}
        // `gap-x-1` and not the mock's whole `gap`: it only ever draws the count
        // beside the monogram, and a row gap stacked in 33 px of chip would clip.
        className={
          compact
            ? "flex h-9 min-w-0 flex-1 flex-wrap items-center justify-center gap-x-1 overflow-hidden rounded-lg border-[1.5px] font-mono text-[10px] font-medium"
            : "flex h-11 flex-1 flex-col items-center justify-center gap-0.5 rounded-lg border-[1.5px] font-mono text-[10px] font-medium"
        }
        style={{
          borderColor: dimmed ? "var(--muted)" : ink,
          background: on ? ringFill(hue) : "transparent",
          color: on ? "#fff" : dimmed ? "var(--fg2)" : ink,
        }}
      >
        <span>{mono}</span>
        {compact ? (
          <span style={{ opacity: on ? RING_COUNT_ON_OPACITY : "var(--ring-count-opacity)" }}>{counts[name]}</span>
        ) : (
          <span className="font-normal">{counts[name]}</span>
        )}
      </button>
    );
  });

  if (!compact) return <div className="flex gap-1.5">{chips}</div>;
  return (
    <div className="flex flex-wrap gap-1.5">
      <div className="flex min-w-0 flex-[1_1_240px] gap-1.5">{chips}</div>
      {trailing && <div className="flex shrink-0 gap-1.5">{trailing}</div>}
    </div>
  );
}
