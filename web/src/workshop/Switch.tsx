/** The knob's two positions in a 52 px track: 3 px in from either end (handoff §7). */
const KNOB_OFF = "3px";
const KNOB_ON = "23px";

export interface SwitchProps {
  /** The stored state — never the state this client asked for. */
  on: boolean;
  label: string;
  /**
   * Refuses to be pressed, but keeps focus — see the handler. Separate from
   * `busy` because the two reasons are not the same fact: a record that cannot
   * be read is inert and is not waiting on anything, and a switch whose write
   * has already been refused is inert with nothing still in flight. A caller
   * whose only reason is the write itself passes the same value to both.
   */
  inert: boolean;
  /** Waiting on the server, as opposed to merely refusing to be pressed. */
  busy: boolean;
  describedBy: string | undefined;
  onToggle: () => void;
}

/**
 * The Workshop's switch, shared by the Triggers bench's rows and the System
 * bench's do-not-disturb. One component because it is one control: the two
 * benches differ in what a press *means* — a trigger's `enabled` is queued for
 * a process that reads it within 60 s, do-not-disturb is a direct write that
 * moves on the read behind it (decision 6) — and not at all in what is drawn.
 * They were two copies of this markup until the copies drifted and took the
 * measured colour pairs below with them.
 *
 * Whether it moves under the finger is the caller's business: `on` is whatever
 * the last read said, so a bench that must not move simply keeps passing the
 * stored value.
 *
 * `aria-disabled` rather than `disabled`, for the reason `ActivityBench`'s
 * `↑ older` button writes out: a control that disables itself under the finger
 * that just pressed it throws focus to `<body>` mid-action. It matters more
 * here — the control is described by the note the press produced, and a
 * description is announced on focus, so a real `disabled` would leave a screen
 * reader with nothing at all about the request it just sent. The handler
 * no-ops instead.
 *
 * The colours are not the handoff's, and the handoff's do not pass. WCAG 1.4.11
 * asks 3:1 of two things here and neither is text: the **boundary** that makes
 * the control findable, and whatever shows **which way it is set**. A `--bg`
 * knob on a `--line` track is 1.18:1 in light and the track's own edge against
 * the page is the same 1.18:1. So the track takes an *inset* `--muted` edge —
 * inset so the 52×32 box and the knob's 3 → 23 px travel stay the handoff's
 * exactly — and the knob takes the token that reads on the track it is on.
 *
 * That inset edge, and not the track fill, is the outermost pixel of the
 * control, so it is the boundary the ratio is owed for. It is measured against
 * both surrounds this switch is used on — the page and a card — in
 * `test/contrast.test.ts`, because the System bench put one inside a `Section`
 * and a pair measured on `--bg` says nothing about `--surface`.
 */
export function Switch({ on, label, inert, busy, describedBy, onToggle }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      // The stored state, always. Never the state this client asked for.
      aria-checked={on}
      aria-label={label}
      aria-busy={busy ? true : undefined}
      aria-disabled={inert ? true : undefined}
      aria-describedby={describedBy}
      onClick={() => {
        // A second tap inside the window can only confuse the reader, and the
        // server would queue a second action against a state neither of us
        // knows. `aria-disabled` does not stop the event, so this does.
        if (!inert) onToggle();
      }}
      className="relative h-8 w-[52px] shrink-0 rounded-2xl border-0 after:absolute after:inset-x-0 after:-inset-y-1.5 after:content-['']"
      style={{
        background: on ? "var(--accent)" : "var(--line)",
        boxShadow: "inset 0 0 0 1px var(--muted)",
      }}
    >
      <span
        aria-hidden="true"
        data-testid="switch-knob"
        className="absolute top-[3px] h-[26px] w-[26px] rounded-full transition-[left] duration-200"
        style={{
          left: on ? KNOB_ON : KNOB_OFF,
          background: on ? "var(--on-accent)" : "var(--fg2)",
        }}
      />
    </button>
  );
}
