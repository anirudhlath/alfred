import { useId } from "react";
import { humaniseTool } from "@/lib/format";
import {
  ROUTINE_STAGES,
  routineDetail,
  routineTrend,
  type Routine,
  type RoutineState,
} from "@/lib/memory";

export interface RoutineRowProps {
  routine: Routine;
  open: boolean;
  /** Called with the routine's name — the hook keys the one open row by it. */
  onToggle: (name: string) => void;
}

/** The sparkline: eight readings at most, 3 px of bar, 2 px of gap, 28 px tall (handoff §6). */
const SPARK_BARS = 8;
const SPARK_HEIGHT = 28;
/** A reading of 0 still gets a bar, or the picture loses a consolidation that happened. */
const SPARK_FLOOR = 2;

/** The stages a routine has already been through are spent, not current, not ahead. */
function barFill(index: number, current: number): string {
  if (index === current) return "var(--accent)";
  return index < current ? "var(--muted)" : "var(--line)";
}

/**
 * The lifecycle (handoff §6): four columns, a 3 px bar each — accent for where
 * the routine is, muted for what it has been through, line for what is ahead —
 * with the stage's own name under it.
 *
 * The names are real text rather than a picture with a visually-hidden caption:
 * a reader gets the four stages and `aria-current` says which one is now, the
 * same fact the accent bar carries for the eye. The bars themselves are
 * `aria-hidden`; they are the colour of the words above them.
 *
 * The labels are `--fg2` rather than the handoff's `muted`: `--muted` is 3.46:1
 * on --bg in the light theme, under AA at 10 px, and `BenchSwitcher` made the
 * same substitution for the same reason.
 */
function Rail({ state }: { state: RoutineState }) {
  const current = ROUTINE_STAGES.indexOf(state);
  return (
    <span data-testid="lifecycle-rail" className="mt-2 grid w-full grid-cols-4 gap-1.5">
      {ROUTINE_STAGES.map((stage, index) => (
        <span
          key={stage}
          data-testid="stage-column"
          // "current step" is how a reader is told which of the four is now;
          // for the eye it is the one accent bar.
          aria-current={index === current ? "step" : undefined}
          className="flex flex-col gap-1"
        >
          <span
            aria-hidden="true"
            data-testid="stage-bar"
            className="h-[3px] w-full rounded-full"
            style={{ background: barFill(index, current) }}
          />
          <span
            className="font-mono text-[10px] leading-[1.4]"
            style={{ color: index === current ? "var(--fg)" : "var(--fg2)" }}
          >
            {stage}
          </span>
        </span>
      ))}
    </span>
  );
}

/**
 * Eight bars of confidence, newest last and the only solid one, captioned with
 * what they are. Fewer than eight readings draws what there is, left-aligned;
 * **no readings draws nothing at all**, because a flat line is a claim about a
 * routine that has never been scored.
 *
 * The caption is `aria-hidden` — the picture's own label says the same thing
 * and the current confidence with it, so a reader would otherwise hear it
 * twice. It counts the bars that are really there rather than the handoff's
 * flat "last 8": three readings captioned as eight would be the invention this
 * bench exists not to make.
 */
function Sparkline({ routine, confidence }: { routine: Routine; confidence: string }) {
  const readings = routine.confidence_history.slice(-SPARK_BARS);
  if (readings.length === 0) return null;
  return (
    <span className="flex flex-col gap-1">
      <span
        role="img"
        aria-label={`confidence over the last ${readings.length} consolidations, now ${confidence}`}
        className="flex items-end gap-[2px]"
        style={{ height: `${SPARK_HEIGHT}px` }}
      >
        {readings.map((value, index) => (
          <span
            // The history is a list of numbers with no identity of its own, and
            // it is replaced whole on every read.
            key={index}
            className="w-[3px] shrink-0 rounded-[1px]"
            style={{
              // Clamped, because a bar taller than the box would crop and a
              // negative one would vanish: the server's confidence is a 0-1
              // score, and a reading outside that is a bug to draw honestly at
              // the rail rather than to trust.
              height: `${Math.max(SPARK_FLOOR, Math.round(Math.min(1, Math.max(0, value)) * SPARK_HEIGHT))}px`,
              background: "var(--accent)",
              opacity: index === readings.length - 1 ? 1 : 0.7,
            }}
          />
        ))}
      </span>
      <span aria-hidden="true" className="t-meta">
        {`confidence, last ${readings.length} consolidations`}
      </span>
    </span>
  );
}

/**
 * One learned routine (handoff §6, Routines). Collapsed: the name, the
 * confidence it is worth now with its move against the last consolidation, and
 * the lifecycle rail. Open: what it was learned from, the steps it would run,
 * and the last eight readings.
 *
 * A rising routine's trend is `--accent-text` (the handoff's "accent when
 * rising", in the readable-as-text token); a falling one takes
 * `.t-meta-strong`'s own `--fg2` and no more. **Never red**: confidence sliding
 * away is the Librarian working, not a fault, and the bench draws no alarm for
 * it. A dormant or archived routine's name steps back to `--fg2` — the
 * handoff's `muted`, in the token that reads at 15 px (see `Rail`).
 */
export function RoutineRow({ routine, open, onToggle }: RoutineRowProps) {
  const trend = routineTrend(routine);
  const panelId = useId();
  const spent = routine.state === "dormant" || routine.state === "archived";

  return (
    <li className="shrink-0" style={{ borderTop: "1px solid var(--line)" }}>
      <button
        type="button"
        aria-expanded={open}
        // Only while open: `aria-controls` pointing at an absent id is invalid.
        aria-controls={open ? panelId : undefined}
        onClick={() => onToggle(routine.name)}
        className="flex min-h-11 w-full flex-col py-3 text-left"
        style={{ color: "var(--fg)" }}
      >
        <span className="flex w-full items-baseline gap-3">
          <span
            className="t-body min-w-0 flex-1 truncate"
            style={spent ? { color: "var(--fg2)" } : undefined}
          >
            {routine.name}
          </span>
          <span
            className="t-meta-strong shrink-0"
            style={trend.rising ? { color: "var(--accent-text)" } : undefined}
          >
            {trend.text}
          </span>
        </span>
        <Rail state={routine.state} />
      </button>
      {open && (
        <div id={panelId} className="mb-3 flex flex-col gap-2">
          <span className="t-meta-strong">{routineDetail(routine)}</span>
          {/* `role="list"` on an `ol` that has lost its markers: Safari drops
              list semantics with the marker. The ordinals are drawn rather than
              left to `::marker` so they take the row's own type, and they are
              `aria-hidden` because an ordered list already announces position.
              `aria-label` because the steps are the second list on the tab. */}
          <ol role="list" aria-label="Steps" className="m-0 flex list-none flex-col gap-1 p-0">
            {routine.steps.map((step, index) => (
              // Steps have no id of their own and the spec is replaced whole
              // on every read, so their position is their identity.
              <li key={index} className="t-row flex flex-wrap items-baseline gap-2">
                <span aria-hidden="true" className="t-meta-strong">{`${index + 1}.`}</span>
                <span>{step.description}</span>
                {/* Load-bearing, not decoration: this is what the step would
                    actually run. A step with no action says nothing here
                    rather than guessing at one. */}
                {step.action && (
                  <span className="t-meta-strong">{humaniseTool(step.action.tool_name)}</span>
                )}
              </li>
            ))}
          </ol>
          <Sparkline routine={routine} confidence={trend.confidence} />
        </div>
      )}
    </li>
  );
}
