import { useId, type ReactNode } from "react";
import { hhmm, isoMs } from "@/lib/format";
import {
  credentialMeta,
  sessionMeta,
  type AttentionDomain,
  type AuthSession,
  type Credential,
} from "@/lib/system";
import { IntegrationRow } from "./IntegrationRow";
import { SystemRow, SystemSection } from "./SystemFrame";
import type { Attention, Credentials, Integrations, Pairing, Sessions } from "./useSystem";

/**
 * The four middle sections of the System bench (handoff §8): who is signed in,
 * what Alfred is connected to, which passkeys open the house, and what the
 * Reflex may act on without asking.
 *
 * One module because they share `SystemFrame`'s card and the same row idiom;
 * four files would be four copies of the same eight imports. The one repeated
 * unit heavy enough to stand on its own — a service's row and the credential
 * form inside it — is `IntegrationRow.tsx`, split out for the reason
 * `MemoryBench` keeps `RoutineRow` next door rather than inside it.
 *
 * Every one of them is a pure view over one slice of `useSystem`'s state: they
 * fetch nothing, which is what lets their tests build a slice rather than a
 * `QueryClient`.
 */

/** What the attention set *is*, said before the chips rather than after them. */
const ATTENTION_INTRO =
  "Alfred acts on these without asking. Everything else it asks about first.";

/**
 * A section's own read or write failure. Rendered inside the card and above the
 * rows, which stay underneath holding what they were last told (spec §5.2): one
 * unreadable list must not take the bench down with it, and it must not quietly
 * read as an empty one either.
 */
function SectionNote({ text, id }: { text: string | null; id?: string }) {
  if (text === null) return null;
  return (
    <p id={id} className="t-meta-strong m-0 border-t border-line px-3 py-2.5 first:border-t-0">
      {text}
    </p>
  );
}

/** A card with nothing in it: one sentence, at the height of the rows it replaces. */
function EmptyRow({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-14 items-center border-t border-line px-3 py-2.5 first:border-t-0">
      <span className="t-body">{children}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

/**
 * One live session. The device that opened it, the line `sessionMeta` builds,
 * and — for every session but the one you are reading this on — a way to end it.
 *
 * `expires_in` is deliberately absent. `core/identity/auth_routes.py` maps a
 * missing key, a persistent key and any non-numeric value all to `0`, so `0`
 * means "no TTL reported" and never "expiring now"; a countdown drawn from it
 * would be a clock counting down from a fact the server never stated.
 */
function SessionRow({
  session,
  now,
  endedAt,
  ending,
  failed,
  onEnd,
}: {
  session: AuthSession;
  now: number;
  /** When the *server* confirmed the end — never when the button was pressed. */
  endedAt: number | undefined;
  ending: boolean;
  /** Why *this* row's end was refused, if it was. */
  failed: string | undefined;
  onEnd: (id: string) => void;
}) {
  const spent = endedAt !== undefined;
  return (
    <SystemRow>
      <div className="flex min-w-0 flex-col gap-0.5 py-2">
        {/* It has happened and it cannot happen again, so the row recedes by
            losing its token rather than by an opacity: a whole-element alpha
            composites every layer under it and is invisible to
            `src/test/contrast.ts`. `TriggerRow` steps a spent one-shot back
            exactly this way. */}
        <span className="t-row truncate" style={spent ? { color: "var(--fg2)" } : undefined}>
          {session.device_name}
        </span>
        <span className="t-meta-strong">{sessionMeta(session, now)}</span>
        {/* `applied`, from the closed vocabulary, and stamped at the answer:
            `useSystem` records the moment the route confirmed the delete, so
            this is never a request time wearing a confirmation's clothes. */}
        {/* **Deviation from the handoff** (`Alfred.dc.html:822`), which
            *replaces* the meta line with `ended HH:MM · applied`. Appended
            instead: the handoff's row is a mock with one session in it, and on
            a real list of four the replacement erases which device was signed
            in where at the moment you most want to check you ended the right
            one. Nothing else on the bench loses a fact to gain one. */}
        {spent && <span className="t-meta-strong">{`ended ${hhmm(endedAt)} · applied`}</span>}
        {/* Beside the row it belongs to and not at the top of the card: with
            four sessions listed and one `End` refused, a section-wide sentence
            names the failure without naming which device is still signed in.
            `useSystem` keys these exactly as it keys the busy flags. */}
        {failed !== undefined && <span className="t-meta-strong">{failed}</span>}
      </div>
      {session.current ? (
        // A label, not a control. You do not end the session you are reading
        // the list on from inside the list — `Sign out on this device`, two
        // sections down, is where that decision belongs.
        <span className="t-meta-strong shrink-0">current</span>
      ) : spent ? null : (
        <button
          type="button"
          // Every row's control says `End`; without the device name a screen
          // reader hears the same word four times and cannot tell them apart.
          aria-label={`End ${session.device_name}`}
          // `aria-disabled` with a no-op, never `disabled`: a disabled control
          // cannot take focus, so nothing it is described by is ever announced.
          aria-disabled={ending ? true : undefined}
          aria-busy={ending ? true : undefined}
          onClick={() => {
            if (!ending) onEnd(session.session_id);
          }}
          className="t-row min-h-11 shrink-0 px-1 text-right"
          // A control that looks like one more label is one nobody presses:
          // 4.85:1 on `--surface` in light (`test/contrast.test.ts`).
          style={{ color: "var(--accent-text)" }}
        >
          End
        </button>
      )}
    </SystemRow>
  );
}

/**
 * Every session `GET /api/auth/sessions` reports, the caller's own included —
 * that row is what says which device you are holding.
 */
export function SessionsSection({ sessions, now }: { sessions: Sessions; now: number }) {
  return (
    <SystemSection title="Sessions">
      <SectionNote text={sessions.error} />
      {sessions.list.length > 0
        ? sessions.list.map((session) => (
            <SessionRow
              key={session.session_id}
              session={session}
              now={now}
              endedAt={sessions.ended[session.session_id]}
              ending={sessions.ending[session.session_id] === true}
              failed={sessions.failed[session.session_id]}
              onEnd={sessions.end}
            />
          ))
        : // Only once a read has actually landed. Before that the list is empty
          // because nothing has answered yet, and `No other sessions.` would be
          // a statement about the house made from no evidence — on the very
          // first paint of the bench, and again for as long as a slow house
          // takes to answer. The section note carries a failure; this sentence
          // is reserved for a successful read that found nothing.
          sessions.read && sessions.error === null && <EmptyRow>No other sessions.</EmptyRow>}
    </SystemSection>
  );
}

// ---------------------------------------------------------------------------
// Connected services
// ---------------------------------------------------------------------------

/**
 * Every adapter and registry-declared service, what its last probe found, and a
 * form for the credentials it runs on.
 *
 * The section owns the list and nothing else: a row is `IntegrationRow`, which
 * is where the schema, the form and the save live.
 */
export function ServicesSection({ integrations }: { integrations: Integrations }) {
  return (
    <SystemSection title="Connected services">
      <SectionNote text={integrations.error} />
      {integrations.list.length > 0
        ? integrations.list.map((entry) => (
            <IntegrationRow
              key={entry.name}
              entry={entry}
              row={integrations.rows[entry.name]}
              save={integrations.saves[entry.name]}
              onSave={integrations.save}
            />
          ))
        : // Gated on a landed read, as `SessionsSection` is: a house that has
          // not answered yet is not a house with nothing connected.
          integrations.read && integrations.error === null && (
            <EmptyRow>No connected services.</EmptyRow>
          )}
    </SystemSection>
  );
}

// ---------------------------------------------------------------------------
// Devices & identity
// ---------------------------------------------------------------------------

/** One registered passkey. The device name, then the whole line the formatter builds. */
function CredentialRow({ passkey, now }: { passkey: Credential; now: number }) {
  return (
    <SystemRow>
      <div className="flex min-w-0 flex-col gap-0.5 py-2">
        <span className="t-row truncate">{passkey.device_name}</span>
        <span className="t-meta-strong">{credentialMeta(passkey, now)}</span>
      </div>
    </SystemRow>
  );
}

/**
 * What a minted pairing code is good for, and for how long. The last clause is
 * not decoration: the code is held until the bench is left and there is no way
 * to ask for the same one again, so a reader who dismisses this screen before
 * typing it has to mint another.
 *
 * The closing time is dropped rather than faked when the server did not send a
 * parseable one — an invented window is worse than none.
 */
function pairingNote(expiresAt: string | null): string {
  const closes = isoMs(expiresAt);
  const tail = "enter this on the new device · not shown again once you leave";
  return closes === null ? tail : `Pairing window closes ${hhmm(closes)} · ${tail}`;
}

/**
 * The passkeys that open the house, a way to add one on another device, and the
 * way off this one.
 *
 * **No delete.** `DELETE /api/auth/credentials/{id}` exists and refuses the last
 * one with a 409, but removing the passkey in your hand is a foot-gun and there
 * is no confirmation design for it; task 11 backlogs it.
 */
export function IdentitySection({
  credentials,
  pairing,
  now,
}: {
  credentials: Credentials;
  pairing: Pairing;
  now: number;
}) {
  const base = useId();
  const codeNoteId = `${base}-code`;
  const mintErrorId = `${base}-mint-error`;
  // A mint answers with a code or with a refusal, and the button that asked is
  // what either one is about — an unwired note at the foot of the card is read
  // by the eye and by nobody else. The hook clears the code before it sends, so
  // in practice only one of the two is ever here; joined rather than chosen so
  // the wiring does not depend on that.
  const mintDescription =
    [pairing.error === null ? null : mintErrorId, pairing.code === null ? null : codeNoteId]
      .filter((id) => id !== null)
      .join(" ") || undefined;

  return (
    <SystemSection title="Devices & identity">
      <SectionNote text={credentials.error} />
      {credentials.list.length > 0
        ? credentials.list.map((passkey) => (
            <CredentialRow key={passkey.credential_id} passkey={passkey} now={now} />
          ))
        : // A read that has not landed is not a house with no passkeys — and
          // this one would be alarming as well as wrong, since a reader holding
          // a passkey would be told there are none.
          credentials.read && credentials.error === null && (
            <EmptyRow>No passkeys registered.</EmptyRow>
          )}

      <SystemRow>
        <button
          type="button"
          aria-disabled={pairing.minting ? true : undefined}
          aria-busy={pairing.minting ? true : undefined}
          aria-describedby={mintDescription}
          onClick={() => {
            if (!pairing.minting) pairing.mint();
          }}
          className="t-row min-h-11 min-w-0 flex-1 text-left"
          style={{ color: "var(--accent-text)" }}
        >
          Add a passkey on another device
        </button>
        {/* A count of a list nobody has read yet is `0 registered`, which is a
            claim rather than a blank. It appears when the read does. */}
        {credentials.read && (
          <span className="t-meta-strong shrink-0">{`${credentials.list.length} registered`}</span>
        )}
      </SystemRow>

      <SectionNote text={pairing.error} id={mintErrorId} />

      {pairing.code !== null && (
        <div className="flex flex-col gap-1.5 border-t border-line px-3 py-3">
          {/* Big, mono and spaced, because it is read off this screen and typed
              into another one — six digits at body size are six digits you get
              wrong. */}
          <span
            data-testid="pairing-code"
            className="font-mono"
            style={{ fontSize: "32px", letterSpacing: "0.18em", color: "var(--fg)" }}
          >
            {pairing.code}
          </span>
          <span id={codeNoteId} className="t-meta-strong">
            {pairingNote(pairing.expiresAt)}
          </span>
        </div>
      )}

      <div className="border-t border-line px-3 py-2.5">
        <button
          type="button"
          aria-disabled={credentials.signingOut ? true : undefined}
          aria-busy={credentials.signingOut ? true : undefined}
          onClick={() => {
            if (!credentials.signingOut) credentials.signOut();
          }}
          className="t-row min-h-11 text-left"
          // Plain text weight, not the accent the two controls above wear. Those
          // two add something; this one takes the house away, and accent here
          // would make the destructive control the brightest thing in the
          // section. `--fg` is 13.70:1 on this card, so it is still plainly a
          // line you can press — the label says what it does.
          style={{ color: "var(--fg)", fontWeight: 400 }}
        >
          Sign out on this device
        </button>
      </div>
    </SystemSection>
  );
}

// ---------------------------------------------------------------------------
// Reflex
// ---------------------------------------------------------------------------

/**
 * One entity, on the side of the line it is currently on. A member is filled and
 * carries an `×` that takes it back to asking; anything merely *seen* is hollow
 * and carries a `+` that lets the Reflex act on it.
 *
 * The fill is `--ink` under `--paper` and the outline a `--muted` edge — the
 * Quiet chips' pairing exactly, and the only one measured against `--surface`
 * rather than the page (`test/contrast.test.ts`): `--line` is 1.09:1 on this
 * card, which is no edge at all.
 */
function AttentionChip({
  entity,
  member,
  busy,
  onPress,
}: {
  entity: string;
  member: boolean;
  busy: boolean;
  onPress: () => void;
}) {
  return (
    <button
      type="button"
      // The symbol is decoration; what the press does has to be in words. The
      // entity leads, so the visible text is inside the accessible name.
      aria-label={`${entity} · ${member ? "ask before acting" : "act without asking"}`}
      aria-disabled={busy ? true : undefined}
      aria-busy={busy ? true : undefined}
      onClick={() => {
        if (!busy) onPress();
      }}
      className="flex min-h-11 items-center gap-1.5 rounded-[10px] border px-2.5 font-mono text-[12px]"
      style={{
        background: member ? "var(--ink)" : "transparent",
        color: member ? "var(--paper)" : "var(--fg2)",
        borderColor: member ? "transparent" : "var(--muted)",
      }}
    >
      {entity}
      <span aria-hidden="true">{member ? "×" : "+"}</span>
    </button>
  );
}

/** One domain: its name, then its members, then everything else it has observed. */
function AttentionDomainBlock({
  domain,
  busy,
  failed,
  allow,
  ask,
}: {
  domain: AttentionDomain;
  busy: boolean;
  /** Why *this* domain's last write was refused, if it was. */
  failed: string | undefined;
  allow: (domain: string, entity: string) => void;
  ask: (domain: string, entity: string) => void;
}) {
  // `seen` is everything observed, members included. Drawing a member twice
  // would offer the same entity both ways at once.
  const rest = domain.seen.filter((entity) => !domain.members.includes(entity));
  return (
    <div className="flex flex-col gap-2 border-t border-line px-3 py-2.5">
      <h4 className="t-meta-strong m-0">{domain.domain}</h4>
      <div className="flex flex-wrap gap-1.5">
        {domain.members.map((entity) => (
          <AttentionChip
            key={entity}
            entity={entity}
            member
            busy={busy}
            onPress={() => ask(domain.domain, entity)}
          />
        ))}
        {rest.map((entity) => (
          <AttentionChip
            key={entity}
            entity={entity}
            member={false}
            busy={busy}
            onPress={() => allow(domain.domain, entity)}
          />
        ))}
      </div>
      {/* Under the chips that failed, not at the top of the card: the busy flag
          is keyed by domain and so is this, or pressing a chip in `climate`
          would wipe the refusal `light` is still showing. */}
      {failed !== undefined && <span className="t-meta-strong">{failed}</span>}
    </div>
  );
}

/**
 * The attention set: what System 1 may act on without asking (deviation 13 — no
 * fidelity-locked design, so it follows the section frame).
 *
 * A failed read and an empty set are two different facts and the section says
 * which: `GET /api/admin/attention` answers 503 when the store is unreachable
 * and an empty list when nothing is configured, and flattening the two would
 * report a house that cannot be read as a house that trusts the Reflex with
 * nothing.
 */
export function ReflexSection({ attention }: { attention: Attention }) {
  return (
    <SystemSection title="Reflex">
      <p className="t-meta-strong m-0 px-3 py-2.5">{ATTENTION_INTRO}</p>
      <SectionNote text={attention.error} />
      {attention.domains.length > 0
        ? attention.domains.map((domain) => (
            <AttentionDomainBlock
              key={domain.domain}
              domain={domain}
              busy={attention.saving[domain.domain] === true}
              failed={attention.failed[domain.domain]}
              allow={attention.allow}
              ask={attention.ask}
            />
          ))
        : // `read` as well as `error === null`: a 503 is not the only way to
          // have nothing to say. Before the first answer the set is empty
          // because nothing has been asked, and this sentence would report a
          // Reflex trusted with nothing.
          attention.read &&
          attention.error === null && <EmptyRow>Nothing is on the attention set yet.</EmptyRow>}
    </SystemSection>
  );
}
