import { useState } from "react";
import { TRIGGER_KINDS } from "@/lib/triggers";
import { TriggerRow } from "./TriggerRow";
import type { KindFilter, Triggers } from "./useTriggers";

export interface TriggersBenchProps {
  triggers: Triggers;
}

/** The chips, in the handoff's order and words (§7). */
const KIND_LABELS: Record<KindFilter, string> = {
  all: "All",
  time: "Time",
  schedule: "Schedule",
  sensor: "Sensor",
  composite: "Composite",
};

/**
 * The Triggers bench (handoff §7; spec §8 phase 3): every stored trigger, under
 * a chip for its kind, each with a switch and a fire that queue rather than
 * apply. A pure view over `useTriggers`' one state object, exactly as
 * `ActivityBench` is over `Activity` — it fetches nothing, which is what lets
 * its tests build a state rather than a `QueryClient`.
 *
 * The bench carries no *connection* region of its own: the Workshop's header
 * status span is the one that survives a change of bench. Its read errors are
 * another matter and have a region below, because nothing else would say them.
 */
export function TriggersBench({ triggers }: TriggersBenchProps) {
  // The clock every stamp on the bench is dated against — read once when the
  // bench mounts, as `MemoryBench` does, so every row agrees about which day
  // "tomorrow" is. A lazy initialiser because the hooks purity rule bans
  // reading the clock in a render body, and because a trigger list is not a
  // thing to re-render once a second.
  const [now] = useState(() => Date.now());
  const empty = triggers.triggers.length === 0;

  return (
    <>
      {/* Filters, not tabs — `aria-pressed` buttons in a group. `tabs.ts`'s
          keyboard is deliberately not reused here: it moves focus between
          `role="tab"` children and activates on arrow, which is a tablist's
          contract and not a toggle group's. The APG walks these with Tab.
          `StreamChips` is not reused either, for a plainer reason: it is eight
          hue-keyed monogram tiles with a count and solo semantics, and these
          are five word labels that pick one filter. They share a height and
          nothing else. */}
      <div
        role="group"
        aria-label="Trigger kinds"
        className="mx-4 mt-2.5 flex h-11 shrink-0 items-center gap-1.5"
      >
        {TRIGGER_KINDS.map((kind) => {
          const active = kind === triggers.kind;
          return (
            <button
              key={kind}
              type="button"
              aria-pressed={active}
              onClick={() => triggers.setKind(kind)}
              // 32 px of chip in a 44 px track: the `after` box adds 6 px above
              // and below, which the row's own height already holds clear.
              // `whitespace-nowrap` because the labels share the width five
              // ways and "Composite" is the one that would otherwise wrap
              // inside a fixed 32 px box on a 360 px screen — whether it fits
              // at all there is for the device checklist, not for a guess here.
              className="relative h-8 flex-1 rounded-lg border px-1 text-[13px] font-medium whitespace-nowrap after:absolute after:inset-x-0 after:-inset-y-1.5 after:content-['']"
              style={{
                background: active ? "var(--ink)" : "transparent",
                color: active ? "var(--paper)" : "var(--fg2)",
                // `--muted` (3.46:1) rather than `--line`, for the reason
                // `StreamChips` gives: five adjacent tap targets edged in
                // `--line` sit at 1.18:1, which is no edge at all.
                borderColor: active ? "transparent" : "var(--muted)",
              }}
            >
              {KIND_LABELS[kind]}
              {/* The *house's* size, beside the chip that means all of it —
                  never the filtered count, which would make the chip that
                  clears the filter report the filter's own answer. The other
                  four would each need their own count, and four numbers that
                  only move together are four things to read. */}
              {kind === "all" && <span className="font-mono"> {triggers.triggers.length}</span>}
            </button>
          );
        })}
      </div>

      {/* Mounted whether or not it has anything to say: VoiceOver can miss a
          region inserted with its text already in it, which is why
          `ActivityBench` and `room/OfflineNote` both keep theirs. `status` and
          not `alert` — a read that failed in the background is news, not an
          interrupt — and `t-meta-strong` because this is the line saying part
          of the list is missing. The rows stay underneath: a failed read is no
          reason to take the last-known list away (spec §5.2). */}
      <p
        role="status"
        aria-label="Read errors"
        className={triggers.error ? "t-meta-strong mx-4 mt-2.5 mb-0" : "sr-only"}
      >
        {triggers.error}
      </p>

      <ul
        role="list"
        aria-label="Triggers"
        aria-busy={triggers.loading}
        className="m-0 flex flex-1 list-none flex-col overflow-y-auto px-4 pt-2 pb-3"
      >
        {triggers.shown.map((trigger) => (
          <TriggerRow
            key={trigger.trigger_id}
            trigger={trigger}
            now={now}
            pending={triggers.pending[trigger.trigger_id]}
            firedAt={triggers.fired[trigger.trigger_id]}
            open={triggers.open === trigger.trigger_id}
            onToggleOpen={triggers.toggleOpen}
            onToggle={triggers.toggle}
            onFire={triggers.fire}
          />
        ))}
        {/* Nothing at all until the server has answered. "No triggers yet." is
            a claim about the house, and a read still in flight is no evidence
            for it — on the one bench whose whole ethic is not saying what it
            does not know. */}
        {triggers.shown.length === 0 && !triggers.loading && (
          <li className="flex flex-col items-center gap-1.5 py-10 text-center">
            {/* Two different facts. A house with no triggers is one thing; a
                chip the reader set three taps ago and has since forgotten is
                another, and the empty list alone does not tell them apart. */}
            <span className="t-body">
              {empty ? "No triggers yet." : `No ${triggers.kind} triggers.`}
            </span>
          </li>
        )}
      </ul>

      <div
        className="flex shrink-0 flex-col gap-1.5 px-4 pt-2.5"
        style={{
          borderTop: "1px solid var(--line)",
          paddingBottom: "calc(12px + env(safe-area-inset-bottom, 0px))",
        }}
      >
        <p className="t-meta-strong m-0">
          Switches are fire-and-forget: the server queues the change and the scheduler picks it up
          within 60 s. A row keeps its old state, with a note, until a fresh read confirms.
        </p>
        {/* Doing real work: there is no create, edit or delete route on this
            API at all, and a reader who does not know that will hunt the bench
            for a button that is not there. */}
        <p className="t-meta-strong m-0">
          Nothing here edits a trigger — ask Alfred to change or remove one.
        </p>
      </div>
    </>
  );
}
