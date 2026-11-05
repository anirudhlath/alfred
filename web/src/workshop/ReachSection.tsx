import { useId, useState } from "react";
import { ReachGates, type ReachGate } from "@/gates/ReachGates";
import { badgeSupported } from "@/lib/badge";
import type { ServiceState } from "@/lib/system";
import { usePush, type Reach } from "@/shell/usePush";
import { StateDot, WORD_COLOUR } from "./IntegrationRow";
import { SystemRow, SystemSection } from "./SystemFrame";
import type { Sessions } from "./useSystem";

/** Where the row's action leads: one of the two gates, or the sync again. */
type Go = ReachGate | "retry";

/**
 * What sits on the row's right: an action, or a state word and its dot — the
 * handoff draws one or the other, never both. The state is Connected services'
 * own (`IntegrationRow.tsx`), so `on` is `ok`'s green, `denied` is `failed`'s
 * accent, and everything that is neither verdict is `unset`'s.
 */
type Right = { action: string; go: Go } | { word: string; state: ServiceState };

/**
 * Each state's sub and right side. `install`, `ask`, `on` and `denied` are
 * `Alfred.dc.html:775`, verbatim; `unavailable`, `unconfigured`, `checking` and
 * `failed` are the plan's deviations D1–D4, which the handoff never drew.
 */
const LINES: Record<Reach["kind"], { sub: string; right: Right }> = {
  install: {
    sub: "unavailable in Safari · add to home screen first",
    right: { action: "Show me how", go: "install" },
  },
  ask: {
    sub: "off · not yet asked · iOS asks once",
    right: { action: "Enable", go: "standalone" },
  },
  on: {
    sub: "on · this iPhone · important and urgent only",
    right: { word: "on", state: "ok" },
  },
  denied: {
    sub: "off · declined in iOS · this app cannot flip it",
    right: { word: "off", state: "failed" },
  },
  unavailable: {
    sub: "unavailable here · this browser cannot receive push",
    right: { word: "off", state: "unset" },
  },
  unconfigured: {
    sub: "unavailable · Web Push is not set up on the house",
    right: { word: "off", state: "unset" },
  },
  checking: {
    sub: "checking · allowed in iOS · registering with the house",
    right: { word: "—", state: "unset" },
  },
  failed: {
    sub: "off · allowed in iOS · the house did not take the registration",
    right: { action: "Try again", go: "retry" },
  },
};

/**
 * What the number on the icon counts, on the `on` line where the icon can show
 * one: the plan's deviation D8, since the handoff's own clause would be false.
 */
const BADGE_CLAUSE = " · badge = notifications since you last opened";

/** Where the switch went, once iOS has it (`Alfred.dc.html:394`, verbatim). */
const DENIED_NOTE =
  "Settings › Alfred › Notifications › Allow. iOS holds this switch now; nothing in here can flip it, and Alfred will not raise it again.";

/**
 * System › Reach (handoff §8, `Alfred.dc.html:385-397`): whether this phone can
 * be reached when the app is closed, and the one step that moves it on — the
 * install gate from a Safari tab, the standalone gate from the home screen, or
 * the sync again after a registration the house did not take.
 *
 * The one section on the bench that reads for itself rather than from
 * `useSystem`: the state is `usePush()`'s, which `PushKeeper` already keeps, and
 * threading it through the bench would only hand it back here. It watches that
 * sync and never starts one by mounting: opening the bench shows a failed sync
 * as `failed` and leaves it there. `Try again` runs it again, as a sign-in, a
 * permission given back or a reconnect does (docs/web-push.md, "From the PWA").
 *
 * It hosts `ReachGates` for its two gates, which rise over the Workshop. Which
 * one is up is held here; asking iOS, and closing when that ends, are theirs.
 *
 * Every line is `.t-meta-strong`, not the handoff's `--muted`: the sub is the
 * row's whole state and the note is the only way back to a switch iOS holds,
 * which is the rule `index.css` sets for meta a reader must take on its own.
 */
export function ReachSection({ sessions }: { sessions: Sessions }) {
  // An observer only: opening the bench is not asking again (`PushOptions`).
  const { reach, retry } = usePush({ syncOnMount: false });
  const [gate, setGate] = useState<ReachGate | null>(null);

  const base = useId();
  const subId = `${base}-sub`;
  const noteId = `${base}-note`;
  const { sub: line, right } = LINES[reach.kind];
  const sub = reach.kind === "on" && badgeSupported() ? `${line}${BADGE_CLAUSE}` : line;
  // A refusal's way back, or the failure as it was said: a browser's own
  // timeout sentence included, since new words here would be new copy.
  const note =
    reach.kind === "denied" ? DENIED_NOTE : reach.kind === "failed" ? reach.detail : null;
  // D5: when this session began, and nothing until the sessions read lands.
  const signedInAt = sessions.list.find((session) => session.current)?.created_at ?? null;

  function go(to: Go) {
    if (to === "retry") retry();
    else setGate(to);
  }

  return (
    <>
      <SystemSection title="Reach">
        <SystemRow>
          <div className="flex min-w-0 flex-col gap-0.5 py-2">
            <span className="t-row">Push notifications</span>
            <span id={subId} className="t-meta-strong">
              {sub}
            </span>
          </div>
          {"action" in right ? (
            // `Alfred.dc.html:390`: an accent text button, 44 px tall, pulled
            // 8 px into the row's padding. `--accent-text` rather than the raw
            // accent, as the Quiet card's `Send them now` takes it.
            <button
              type="button"
              aria-describedby={note === null ? subId : `${subId} ${noteId}`}
              onClick={() => go(right.go)}
              className="-mr-2 h-11 shrink-0 px-3 text-[13px] font-medium"
              style={{ color: "var(--accent-text)" }}
            >
              {right.action}
            </button>
          ) : (
            <span className="flex shrink-0 items-center gap-2">
              <span className="t-meta-strong" style={{ color: WORD_COLOUR[right.state] }}>
                {right.word}
              </span>
              <StateDot state={right.state} />
            </span>
          )}
        </SystemRow>
        {note !== null && (
          <p
            id={noteId}
            className="t-meta-strong m-0 border-t border-line px-3 pt-2.5 pb-3 leading-[1.6]"
          >
            {note}
          </p>
        )}
      </SystemSection>

      <ReachGates gate={gate} signedInAt={signedInAt} onClose={() => setGate(null)} />
    </>
  );
}
