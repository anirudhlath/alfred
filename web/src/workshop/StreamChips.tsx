import { ring, STREAM_INFO, STREAMS, type StreamName } from "@/lib/streams";

export interface StreamChipsProps {
  counts: Record<StreamName, number>;
  solo: StreamName | null;
  onSolo: (name: StreamName | null) => void;
}

/**
 * Eight equal chips, monogram over count (handoff, Activity footer). Tapping
 * one solos its stream — filled with its ring, the other seven at 35 % —
 * and tapping it again clears. The count is what the bench holds, not what
 * the server has.
 *
 * More precisely it is loaded depth per stream, which is why a count can read
 * higher than the rows on screen: with all eight shown the list stops at the
 * shared horizon, so a stream `↑ older` paged deeper than the rest holds
 * entries the merged view is not yet allowed to show. Solo it to see them all.
 */
export function StreamChips({ counts, solo, onSolo }: StreamChipsProps) {
  return (
    <div className="flex justify-between gap-1.5">
      {STREAMS.map((name) => {
        const { mono, hue } = STREAM_INFO[name];
        const color = ring(hue);
        const on = solo === name;
        return (
          <button
            key={name}
            type="button"
            // The two spans are flex items and so read as `HS 18` in a browser,
            // but the name is stated rather than left to the stylesheet: read
            // as inline text it would run together into `HS18`.
            aria-label={`${mono} ${counts[name]}`}
            aria-pressed={on}
            onClick={() => onSolo(on ? null : name)}
            className="flex h-11 flex-1 flex-col items-center justify-center gap-0.5 rounded-lg border-[1.5px] p-0 font-mono text-[10px] font-medium"
            style={{
              borderColor: color,
              background: on ? color : "transparent",
              color: on ? "#fff" : color,
              opacity: solo === null || on ? 1 : 0.35,
            }}
          >
            <span>{mono}</span>
            <span className="font-normal opacity-80">{counts[name]}</span>
          </button>
        );
      })}
    </div>
  );
}
