export interface UpdateNoteProps {
  ready: boolean;
  onReload: () => void;
}

/**
 * A changed `/sw.js` has installed behind the running worker and is waiting.
 * Decision 7: it does not take over by itself, because a swap under a running
 * conversation loses what is on screen — so this is the switch. Left alone, the
 * new worker waits until every page the old one controls has closed, and an
 * installed PWA can stay open for days.
 *
 * It is not how a deploy arrives. `/sw.js` is byte-identical across most
 * deploys, so most install no worker and never reach this note: an open app
 * takes them at its next navigation, which sw.js answers network-first. That is
 * how an installed iOS PWA gets a new build without reinstalling; this note is
 * how it gets a new worker without waiting.
 *
 * The handoff has no copy for this: no scene, no gate variant, no row type. The
 * sentence is invented, and its second clause is the handoff's own, verbatim
 * from the install gate's foot (`Alfred.dc.html:669`).
 *
 * `--accent`, not `--green`: phase 3's rule is that accent is a decision waiting
 * on the world, and green is only `alive` and `applied`. An update waiting on
 * the user is exactly the first of those. The dot is decorative — hidden from
 * assistive tech, beside a sentence that says all it means — which is what lets
 * the raw accent sit on the card at 2.17:1 in light, where a dot that carried
 * state on its own would take `--accent-text`.
 *
 * The live region stays mounted while there is nothing to say, empty and
 * `sr-only`, for the reason `OfflineNote` does.
 *
 * `Reload` is 13 px text, 42 px wide. Its box is fixed at 20 px tall, and its
 * `::after` reaches 17 px above it, 7 px below and 8 px past each side: 44 by 58
 * to tap. On its bare 18.2 px line box the same reach would make 42.2. Its
 * `-my-px` lays that box out at 18 px, under the sentence's 18.2, so a one-line
 * pill is as tall as `OfflineNote`'s rather than 1.8 px taller.
 *
 * The target is lopsided because nothing under the pill is safe to reach into.
 * With no row below the note, the header ends at the pill, and the Timeline — a
 * later `relative z-[1]` sibling — paints over the header and takes every tap
 * past that edge. So the 7 px end just inside the pill, whose bottom edge is
 * 7.1 px under the box on one line, and the 17 px find the rest in the 12 px gap
 * above: 9.9 px past the pill, about 2 px short of the row above. Wrapped onto
 * two lines it reaches 0.8 px above the pill; onto three, not past it at all.
 */
export function UpdateNote({ ready, onReload }: UpdateNoteProps) {
  return (
    <div
      role="status"
      className={
        ready
          ? "mt-2 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13px] leading-[1.4]"
          : "sr-only"
      }
      style={ready ? { background: "var(--surface)" } : undefined}
    >
      {ready ? (
        <>
          <span
            aria-hidden="true"
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ background: "var(--accent)" }}
          />
          <span>A newer Alfred is waiting. Nothing is lost; I pick up where we left off.</span>
          <button
            type="button"
            onClick={onReload}
            className="relative -my-px ml-auto h-5 shrink-0 font-medium after:absolute after:-inset-x-2 after:-top-[17px] after:-bottom-[7px] after:content-['']"
            style={{ color: "var(--accent-text)" }}
          >
            Reload
          </button>
        </>
      ) : null}
    </div>
  );
}
