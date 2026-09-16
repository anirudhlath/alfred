import { useId } from "react";
import { hhmm, pastLabel, rawCall } from "@/lib/format";
import { triggerKind, triggerMeta, type Trigger } from "@/lib/triggers";
import type { Pending, PendingKind } from "./useTriggers";

export interface TriggerRowProps {
  trigger: Trigger;
  /**
   * The bench's one clock, in epoch ms. A prop rather than a `Date.now()` in
   * here for the two reasons `MemoryBench` holds its own: the hooks purity rule
   * bans reading the clock in render, and a trigger list spans weeks in both
   * directions, so every row on the bench has to date itself against the same
   * instant or two rows a pixel apart disagree about which day `tomorrow` is.
   */
  now: number;
  /** This row's request, while one is in flight or inside its 60 s window. */
  pending?: Pending;
  /** When *this client* last queued a fire — never when the trigger ran. */
  firedAt?: number;
  open: boolean;
  /** Called with the `trigger_id` — the hook keys the one open row by it. */
  onToggleOpen: (id: string) => void;
  onToggle: (trigger: Trigger) => void;
  onFire: (trigger: Trigger) => void;
}

/** The knob's two positions in a 52 px track: 3 px in from either end (handoff §7). */
const KNOB_OFF = "3px";
const KNOB_ON = "23px";

/** The handoff's sentence about what a fire does and does not tell us (§7). */
const FIRE_TAIL = "look for trigger.fired on the events stream to know it ran";
const FIRE_NOTE = `queued only; ${FIRE_TAIL}`;

/**
 * What a refusal leaves behind. A toggle that did not land leaves the stored
 * setting where it was; a *fire* has no setting to leave — it either queued or
 * it did not, and nothing about the trigger changed either way.
 */
const REFUSED_TAIL: Record<PendingKind, string> = {
  enabling: "the scheduler still has the old setting",
  disabling: "the scheduler still has the old setting",
  firing: "nothing was queued",
};

/** A server dump, as `EventRow` draws an event's: the same two lines, named once. */
const DUMP =
  "m-0 rounded-lg px-3 py-2.5 font-mono text-[11px] leading-[1.55] break-words whitespace-pre-wrap";
const DUMP_STYLE = { background: "var(--surface)", color: "var(--fg2)" } as const;

/** `20:52` · `20:52 yesterday` — `lib/format`'s own stamp for a moment already behind. */
function stamp(iso: string, now: number): string {
  const date = new Date(iso);
  // A record whose stamp will not parse says so rather than printing `NaN undefined`.
  if (Number.isNaN(date.getTime())) return "--:--";
  return pastLabel(date, new Date(now));
}

/**
 * What the meta line deliberately leaves out (`lib/triggers.ts`): who asked for
 * this trigger and when, how loudly it speaks, and whether it has ever gone
 * off. `never fired` rather than a missing clause — a trigger that has never
 * run is a fact about it, not an absence.
 */
function triggerDetail(trigger: Trigger, now: number): string {
  return [
    `created by ${trigger.created_by}`,
    stamp(trigger.created_at, now),
    `urgency ${trigger.urgency}`,
    trigger.last_fired === null ? "never fired" : `last fired ${stamp(trigger.last_fired, now)}`,
  ].join(" · ");
}

/** `500 · <detail> · …` with the clause the server did not send dropped, never blank. */
function corruptNote(detail: string | undefined): string {
  return ["500", detail, "the scheduler skips it", "fix in the store or delete"]
    .filter((clause): clause is string => Boolean(clause))
    .join(" · ");
}

/**
 * The row's own note: one line, under the meta, saying what this client has
 * asked for and does not yet know the outcome of.
 *
 * `queued` is accent, because it is a decision waiting on the world rather than
 * something that went well (decision 7). A refusal is settled — it takes
 * `.t-meta-strong`'s own --fg2 and no more, and there is no red on this bench.
 *
 * A *fire* has no line here: its note belongs under the button that sent it,
 * where the handoff puts it, and the control and its note travel together.
 * There is deliberately no `firing · takes effect within 60 s` third sentence —
 * the 60 s is the *enabled-cache* window and says nothing about a manual fire.
 * A fire that was *refused* does appear, because a refusal is news about the
 * row whether or not the reader has it open.
 */
function rowNote(pending: Pending | undefined): { text: string; queued: boolean } | null {
  if (pending === undefined) return null;
  if (pending.error !== undefined) {
    // The status when a server answered with one; otherwise what we do have. A
    // request that never arrived must not print a number nobody sent.
    const head = pending.status ?? pending.error;
    return { text: `${head} · that did not land · ${REFUSED_TAIL[pending.kind]}`, queued: false };
  }
  if (pending.kind === "firing") return null;
  return {
    text: `queued ${hhmm(pending.at)} · ${pending.kind} · takes effect within 60 s`,
    queued: true,
  };
}

interface SwitchProps {
  on: boolean;
  label: string;
  /** Inert, but still focusable — see the comment on the handler. */
  inert: boolean;
  /** Waiting on the server, as opposed to merely refusing to be pressed. */
  busy: boolean;
  describedBy: string | undefined;
  onToggle: () => void;
}

/**
 * The switch that **does not move when you press it** (handoff §7, decision 6).
 * `POST …/enabled` answers `queued`: the triggers process applies the change
 * inside its own 60 s cache window and nothing tells this client when. So
 * `aria-checked` keeps reporting the `enabled` the last read gave us, the knob
 * keeps its position, and the note underneath carries the state that was
 * *asked for* — which is the only thing we know.
 *
 * `aria-disabled` rather than `disabled`, for the reason `ActivityBench`'s
 * `↑ older` button writes out: a control that disables itself under the finger
 * that just pressed it throws focus to `<body>` mid-action. It matters more
 * here than there — the control is described by the queued note, and a
 * description is announced on focus, so a real `disabled` would leave a screen
 * reader with nothing at all about the request it just sent, for the whole
 * window. The handler no-ops instead.
 *
 * The colours are not the handoff's, and the handoff's do not pass: a `--bg`
 * knob on a `--line` track is 1.18:1 in light and the track's own edge against
 * the page is the same 1.18:1, where WCAG 1.4.11 asks 3:1 of both a control's
 * boundary and whatever shows which way it is set. The track takes an *inset*
 * `--muted` edge — inset so the 52×32 box and the knob's 3 → 23 travel are the
 * handoff's exactly — and the knob takes the token that reads on the track it
 * is on. All three pairs are measured in `test/contrast.test.ts`.
 */
function Switch({ on, label, inert, busy, describedBy, onToggle }: SwitchProps) {
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
      className="relative mt-[11px] h-8 w-[52px] shrink-0 rounded-2xl border-0 after:absolute after:inset-x-0 after:-inset-y-1.5 after:content-['']"
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

/**
 * Deviation 6's one reachable case (handoff §7). `GET /api/admin/triggers` drops
 * records it cannot parse, so a corrupt one is never in the list — it can only
 * be met by acting on a record that decayed since the read, with either control.
 *
 * It **self-dismisses** when the hook's window closes 60 s after the tap, and
 * that is deliberate: a client-side claim must not outlive the evidence for it,
 * and the next read either shows the trigger again or does not.
 */
function CorruptCard({ detail, noteId }: { detail: string | undefined; noteId: string }) {
  return (
    <div
      className="mb-[11px] flex flex-col gap-1 rounded-xl px-3 py-2"
      style={{ background: "var(--surface)" }}
    >
      <span className="t-body">This record can&apos;t be read.</span>
      <span id={noteId} className="t-meta-strong">
        {corruptNote(detail)}
      </span>
    </div>
  );
}

/**
 * `Fire now` and the sentence under it, which travel together: the note is the
 * only thing on the bench that says what a fire does and does not tell us, and
 * it is the button's own description.
 *
 * Never `Fired`. The server queued it; what the trigger then did is on the
 * events stream and nowhere in this response.
 */
function FireControl({
  firedAt,
  inert,
  onFire,
}: {
  firedAt: number | undefined;
  inert: boolean;
  onFire: () => void;
}) {
  const noteId = useId();
  const queued = firedAt !== undefined;
  return (
    <div className="flex flex-col gap-1">
      {/* `border-line` is 1.18:1 on the page like the switch's track was, and
          stays: 1.4.11 asks for a boundary where nothing else identifies the
          control, and this one carries its own name in 14.82:1 text. The same
          reasoning `EventRow`'s pills already run on. */}
      <button
        type="button"
        aria-disabled={inert ? true : undefined}
        aria-describedby={noteId}
        onClick={() => {
          if (!inert) onFire();
        }}
        className="h-11 self-start rounded-[22px] border border-line bg-transparent px-[18px] text-[14px] font-medium"
        style={{ color: "var(--fg)" }}
      >
        {queued ? "Fire again" : "Fire now"}
      </button>
      <span
        id={noteId}
        className="t-meta-strong"
        style={queued ? { color: "var(--accent-text)" } : undefined}
      >
        {queued ? `queued ${hhmm(firedAt)} · ${FIRE_TAIL}` : FIRE_NOTE}
      </span>
    </div>
  );
}

/**
 * One stored trigger (handoff §7). The kind, the name, what it waits for, and a
 * switch that reports what the server last said rather than what was asked of
 * it. Nothing here claims an outcome the server did not confirm.
 */
export function TriggerRow({
  trigger,
  now,
  pending,
  firedAt,
  open,
  onToggleOpen,
  onToggle,
  onFire,
}: TriggerRowProps) {
  const base = useId();
  const panelId = `${base}-panel`;
  // One slot, one id: the card and the note are mutually exclusive, and
  // whichever is on screen is what describes the switch above it.
  const noteId = `${base}-note`;

  const corrupt = pending !== undefined && pending.status === 500;
  // A queued *fire* says nothing about the switch's own state, so it does not
  // hold the switch — which also means no control is ever left inert on a
  // collapsed row with its reason folded away inside the panel.
  const switching = pending !== undefined && pending.kind !== "firing";
  const note = corrupt ? null : rowNote(pending);
  // It has happened and it cannot happen again. It recedes by losing its hue
  // rather than by opacity: composited, `.55` takes the meta line to 2.71:1 and
  // the name to 3.63:1 in light, and a whole-row alpha is invisible to
  // `contrast.ts`. `RoutineRow` steps a spent routine's name back the same way.
  const spent = trigger.one_shot && trigger.last_fired !== null;

  return (
    <li className="shrink-0" style={{ borderTop: "1px solid var(--line)" }}>
      <div className="flex items-start gap-3">
        <button
          type="button"
          aria-expanded={open}
          // Only while open: `aria-controls` pointing at an absent id is invalid.
          aria-controls={open ? panelId : undefined}
          onClick={() => onToggleOpen(trigger.trigger_id)}
          className="flex min-h-11 min-w-0 flex-1 flex-col py-[11px] text-left"
          style={{ color: "var(--fg)" }}
        >
          {/* `--accent-text`, not the raw accent, which is 2.34:1 on paper
              (index.css, --accent-text) — and neither, once it is spent. */}
          <span
            className="font-mono text-[10px] leading-[1.4]"
            style={{ color: spent ? "var(--fg2)" : "var(--accent-text)" }}
          >
            {triggerKind(trigger)}
          </span>
          <span
            className="t-row w-full truncate"
            style={spent ? { color: "var(--fg2)" } : undefined}
          >
            {trigger.name}
          </span>
          {/* The card below replaces this line when the record cannot be read:
              the meta is built from the very conditions the server has just
              said it cannot parse. The name stays — a card that does not say
              *which* record is broken is not worth drawing. */}
          {!corrupt && <span className="t-meta-strong">{triggerMeta(trigger, now)}</span>}
        </button>
        <Switch
          on={trigger.enabled}
          label={trigger.name}
          inert={corrupt || switching}
          // Waiting, as opposed to merely refusing to be pressed: a refusal has
          // already landed and a record that cannot be read is not being updated.
          busy={switching && pending.error === undefined}
          describedBy={corrupt || note ? noteId : undefined}
          onToggle={() => onToggle(trigger)}
        />
      </div>
      {corrupt && <CorruptCard detail={pending.error} noteId={noteId} />}
      {note && (
        <p
          id={noteId}
          className="t-meta-strong mt-0 mb-[11px]"
          style={note.queued ? { color: "var(--accent-text)" } : undefined}
        >
          {note.text}
        </p>
      )}
      {open && (
        <div id={panelId} className="mb-3 flex flex-col gap-2">
          <span className="t-meta-strong">{triggerDetail(trigger, now)}</span>
          {trigger.action ? (
            <pre data-testid="trigger-action" className={DUMP} style={DUMP_STYLE}>
              {rawCall(trigger.action.tool_name, trigger.action.parameters)}
            </pre>
          ) : (
            // A trigger with nothing bound still does something: it speaks.
            <span className="t-meta-strong">no action · notification only</span>
          )}
          {/* The conditions as the server holds them — no field hidden or
              renamed, the same bargain `EventRow` makes with an event. */}
          <pre data-testid="trigger-conditions" className={DUMP} style={DUMP_STYLE}>
            {JSON.stringify(trigger.conditions, null, 2)}
          </pre>
          <FireControl
            firedAt={firedAt}
            // Any request at all holds the fire: a second one would take the
            // row's one note, and with it the record of the first.
            inert={pending !== undefined}
            onFire={() => onFire(trigger)}
          />
        </div>
      )}
    </li>
  );
}
