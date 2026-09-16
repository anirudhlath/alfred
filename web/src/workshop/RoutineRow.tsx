import { Fragment, useId } from "react";
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

/** The sparkline: eight readings at most, 3 px of bar, 2 px of gap, 20 px tall. */
const SPARK_BARS = 8;
const SPARK_HEIGHT = 20;
/** A reading of 0 still gets a bar, or the picture loses a consolidation that happened. */
const SPARK_FLOOR = 2;

/**
 * The lifecycle, as a picture: four dots in `ROUTINE_STAGES` order joined by a
 * hairline, filled up to the stage the routine is at and hollow after it. The
 * whole rail is `aria-hidden` — the row says `stage: dormant` in words beside
 * it, and a rail spelled out dot by dot would say the same thing worse.
 */
function Rail({ state }: { state: RoutineState }) {
  const current = ROUTINE_STAGES.indexOf(state);
  return (
    <span
      aria-hidden="true"
      data-testid="lifecycle-rail"
      className="flex w-[68px] shrink-0 items-center"
    >
      {ROUTINE_STAGES.map((stage, index) => (
        <Fragment key={stage}>
          {index > 0 && <span className="h-px flex-1" style={{ background: "var(--line)" }} />}
          <span
            data-testid="stage-dot"
            className="h-1.5 w-1.5 shrink-0 rounded-full border"
            style={{
              background:
                index < current ? "var(--line)" : index === current ? "var(--accent)" : "transparent",
              // The ring is the hollow dot's whole shape; a filled one has no
              // need of it and borrows the fill's own edge.
              borderColor: index <= current ? "transparent" : "var(--line)",
            }}
          />
        </Fragment>
      ))}
    </span>
  );
}

/**
 * Eight bars of confidence, newest last and the only solid one. Fewer than
 * eight readings draws what there is, left-aligned; **no readings draws
 * nothing at all**, because a flat line is a claim about a routine that has
 * never been scored.
 */
function Sparkline({ routine }: { routine: Routine }) {
  const readings = routine.confidence_history.slice(-SPARK_BARS);
  if (readings.length === 0) return null;
  return (
    <span
      role="img"
      aria-label={`confidence over the last ${readings.length} consolidations, now ${routine.confidence.toFixed(2)}`}
      className="flex h-5 items-end gap-[2px]"
    >
      {readings.map((value, index) => (
        <span
          // The history is a list of numbers with no identity of its own, and
          // it is replaced whole on every read.
          key={index}
          className="w-[3px] shrink-0 rounded-[1px]"
          style={{
            height: `${Math.max(SPARK_FLOOR, Math.round(Math.min(1, Math.max(0, value)) * SPARK_HEIGHT))}px`,
            background: "var(--accent)",
            opacity: index === readings.length - 1 ? 1 : 0.35,
          }}
        />
      ))}
    </span>
  );
}

/**
 * One learned routine (handoff §6, Routines). Collapsed: the name, the
 * confidence it is worth now with its move against the last consolidation, and
 * the lifecycle rail. Open: what it was learned from, the steps it would run,
 * and the last eight readings.
 *
 * A falling routine is `--fg2`, never red: confidence sliding away is the
 * Librarian working, and the bench draws no alarm for it. Rising is
 * `--green-text` rather than `--green` — green as a word is 2.29:1 on paper
 * (index.css, --green-text).
 */
export function RoutineRow({ routine, open, onToggle }: RoutineRowProps) {
  const trend = routineTrend(routine);
  const panelId = useId();

  return (
    <li className="shrink-0" style={{ borderTop: "1px solid var(--line)" }}>
      <button
        type="button"
        aria-expanded={open}
        // Only while open: `aria-controls` pointing at an absent id is invalid.
        aria-controls={open ? panelId : undefined}
        onClick={() => onToggle(routine.name)}
        className="flex min-h-11 w-full items-center gap-3 py-3 text-left"
        style={{ color: "var(--fg)" }}
      >
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="t-body truncate">{routine.name}</span>
          <span
            className="t-meta-strong"
            style={{ color: trend.rising ? "var(--green-text)" : "var(--fg2)" }}
          >
            {trend.text}
          </span>
        </span>
        <span className="sr-only">{`stage: ${routine.state}`}</span>
        <Rail state={routine.state} />
      </button>
      {open && (
        <div id={panelId} className="mb-3 flex flex-col gap-2">
          <span className="t-meta-strong">{routineDetail(routine)}</span>
          {/* `role="list"` on an `ol` that has lost its markers: Safari drops
              list semantics with the marker. `aria-label` because the steps
              are the second list on the Routines tab. */}
          <ol role="list" aria-label="Steps" className="m-0 flex list-none flex-col gap-1 p-0">
            {routine.steps.map((step, index) => (
              // Steps have no id of their own and the spec is replaced whole
              // on every read, so their position is their identity.
              <li key={index} className="t-row flex flex-wrap items-baseline gap-2">
                <span>{step.description}</span>
                {/* Load-bearing, not decoration: this is what the step would
                    actually run. A step with no action says nothing here
                    rather than guessing at one. */}
                {step.action && <span className="t-meta-strong">{humaniseTool(step.action.tool_name)}</span>}
              </li>
            ))}
          </ol>
          <Sparkline routine={routine} />
        </div>
      )}
    </li>
  );
}
