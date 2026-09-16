import { useId } from "react";
import { hhmm, pastLabel, rawCall } from "@/lib/format";
import { triggerKind, triggerMeta, type Trigger } from "@/lib/triggers";
import { Switch } from "./Switch";
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
        {/* The switch that **does not move when you press it** (handoff §7,
            decision 6): `POST …/enabled` answers `queued`, the triggers process
            applies the change inside its own 60 s cache window, and nothing
            tells this client when. So `on` keeps reporting the `enabled` the
            last read gave us and the note underneath carries what was *asked
            for*, which is the only thing we know.

            The nudge is the row's, not the switch's: this row hangs its
            controls from `items-start` so a two-line name does not centre the
            switch against it. */}
        <span className="mt-[11px] flex shrink-0">
          <Switch
            on={trigger.enabled}
            label={trigger.name}
            inert={corrupt || switching}
            // Waiting, as opposed to merely refusing to be pressed: a refusal
            // has already landed and a record that cannot be read is not being
            // updated.
            busy={switching && pending.error === undefined}
            describedBy={corrupt || note ? noteId : undefined}
            onToggle={() => onToggle(trigger)}
          />
        </span>
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
