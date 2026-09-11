import { ringFill, ringText, STREAM_INFO, STREAMS, type StreamName, streamLabel } from "@/lib/streams";

export interface StreamChipsProps {
  counts: Record<StreamName, number>;
  solo: StreamName | null;
  onSolo: (name: StreamName | null) => void;
}

/**
 * Eight equal chips, monogram over count (handoff, Activity footer). Tapping
 * one solos its stream and tapping it again clears. The count is what the
 * bench holds, not what the server has.
 *
 * More precisely it is loaded depth per stream, which is why a count can read
 * higher than the rows on screen: with all eight shown the list stops at the
 * shared horizon, so a stream `↑ older` paged deeper than the rest holds
 * entries the merged view is not yet allowed to show. Solo it to see them all.
 *
 * The other seven recede by losing their colour — border to `--line`, label to
 * `--fg2` — rather than by going translucent. The count is the chip's only
 * data: 35 % opacity took it to 1.31:1 in light theme, and `--muted` would
 * still leave it at 3.46:1, under AA for 10 px text.
 */
export function StreamChips({ counts, solo, onSolo }: StreamChipsProps) {
  return (
    <div className="flex gap-1.5">
      {STREAMS.map((name) => {
        const { mono, hue } = STREAM_INFO[name];
        const on = solo === name;
        const dimmed = solo !== null && !on;
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
            className="flex h-11 flex-1 flex-col items-center justify-center gap-0.5 rounded-lg border-[1.5px] font-mono text-[10px] font-medium"
            style={{
              borderColor: dimmed ? "var(--line)" : ink,
              background: on ? ringFill(hue) : "transparent",
              color: on ? "#fff" : dimmed ? "var(--fg2)" : ink,
            }}
          >
            <span>{mono}</span>
            <span className="font-normal">{counts[name]}</span>
          </button>
        );
      })}
    </div>
  );
}
