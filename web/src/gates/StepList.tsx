import type { CSSProperties } from "react";

export type StepState = "done" | "current" | "todo";

export interface ProgressStep {
  label: string;
  meta?: string;
  state: StepState;
}

export interface ToggleStep {
  id: string;
  label: string;
  allowed: boolean;
}

export type StepListProps =
  | { variant: "progress"; steps: ProgressStep[] }
  | { variant: "toggle"; steps: ToggleStep[]; onToggle: (id: string) => void };

const ROW: CSSProperties = { borderTop: "1px solid var(--line)" };

function ring(filled: boolean, border: string): CSSProperties {
  return {
    borderColor: border,
    background: filled ? border : "transparent",
  };
}

/** What a screen reader hears where a sighted user sees the ring's colour. */
const STATE_WORD: Record<StepState, string | null> = {
  done: "done",
  current: null, // aria-current="step" says it
  todo: "not done",
};

/** 22 px ring: accent filled = done/allowed, fg ring = current, line = ahead/ask me. */
function Ring({ style }: { style: CSSProperties }) {
  return (
    <span
      aria-hidden="true"
      className="box-border block h-[22px] w-[22px] shrink-0 rounded-full border-[1.5px]"
      style={style}
    />
  );
}

/**
 * Both variants carry an explicit `role="list"`: preflight strips list-style,
 * and WebKit strips the list semantics with it — VoiceOver would not say
 * "3 items" without the attribute.
 */
export function StepList(props: StepListProps) {
  if (props.variant === "progress") {
    return (
      <ol role="list" className="flex flex-col pt-2">
        {props.steps.map((step) => (
          <li
            key={step.label}
            data-step-state={step.state}
            aria-current={step.state === "current" ? "step" : undefined}
            className="flex min-h-12 items-center gap-3"
            style={ROW}
          >
            <Ring
              style={
                step.state === "done"
                  ? ring(true, "var(--accent)")
                  : step.state === "current"
                    ? ring(false, "var(--fg)")
                    : ring(false, "var(--line)")
              }
            />
            <span
              className="t-row flex-1"
              style={{ color: step.state === "todo" ? "var(--muted)" : "var(--fg)" }}
            >
              {step.label}
            </span>
            {STATE_WORD[step.state] ? <span className="sr-only">{STATE_WORD[step.state]}</span> : null}
            {step.meta ? <span className="t-meta">{step.meta}</span> : null}
          </li>
        ))}
      </ol>
    );
  }

  return (
    <ul role="list" className="flex flex-col pt-2">
      {props.steps.map((step) => (
        <li key={step.id}>
          <button
            type="button"
            aria-pressed={step.allowed}
            onClick={() => props.onToggle(step.id)}
            className="flex min-h-12 w-full items-center gap-3 border-0 bg-transparent text-left"
            style={ROW}
          >
            <Ring style={step.allowed ? ring(true, "var(--accent)") : ring(false, "var(--line)")} />
            <span className="t-row flex-1" style={{ color: "var(--fg)" }}>
              {step.label}
            </span>
            <span className="t-meta">{step.allowed ? "allowed" : "ask me"}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
