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

export function StepList(props: StepListProps) {
  if (props.variant === "progress") {
    return (
      <div className="flex flex-col pt-2">
        {props.steps.map((step) => (
          <div
            key={step.label}
            data-step-state={step.state}
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
            {step.meta ? <span className="t-meta">{step.meta}</span> : null}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col pt-2">
      {props.steps.map((step) => (
        <button
          key={step.id}
          type="button"
          aria-pressed={step.allowed}
          data-allowed={step.allowed}
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
      ))}
    </div>
  );
}
