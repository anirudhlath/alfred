import { useId, useState } from "react";
import {
  serviceNote,
  type Integration,
  type ServiceRow,
  type ServiceState,
} from "@/lib/system";
import type { CredentialSave } from "./useSystem";

export interface IntegrationRowProps {
  entry: Integration;
  /** What the last probe found. Absent only before `serviceRows` has seen this name. */
  row: ServiceRow | undefined;
  /** This client's last credential save for this name, if it has made one. */
  save: CredentialSave | undefined;
  onSave: (name: string, values: Record<string, string>) => void;
}

/** A name before any probe has answered for it: a round trip in progress, which is the truth. */
const UNPROBED: ServiceRow = { state: "testing", latency: null, status: null };

/**
 * The colour each state word wears. `--fg2` and **not** the handoff's `--muted`
 * for `unset`: `--muted` is 3.20:1 on `--surface` in light, under AA, and this
 * word is the entire state of the row rather than decoration beside it
 * (`test/contrast.test.ts` measures both).
 *
 * `testing` and `queued` take `--fg2` as well. Neither is a verdict — one is a
 * probe in flight and the other this client's own claim that a save landed —
 * and giving either the accent would spend the one colour on this card that
 * means "look here" on a row that is merely busy.
 */
const WORD_COLOUR: Record<ServiceState, string> = {
  ok: "var(--green-text)",
  failed: "var(--accent-text)",
  unset: "var(--fg2)",
  testing: "var(--fg2)",
  queued: "var(--fg2)",
};

/** What a refusal from the trusted-network gate means, in the words the reader needs. */
const GATED = "Credentials can only be changed from the home network.";

/**
 * The dot beside the state word, in `MemoryBench`'s idiom: **filled when the
 * service is answering and an outline when it is not**. Fill versus outline
 * rather than hue, because `--green` against `--muted` is 1.22:1 dark and
 * 1.51:1 light — two circles of near-identical luminance, and a reader who
 * cannot separate those hues would have nothing at all.
 *
 * `aria-hidden` and redundant: the word beside it says the same thing.
 */
function StateDot({ state }: { state: ServiceState }) {
  const ok = state === "ok";
  return (
    <span
      aria-hidden="true"
      data-testid="service-dot"
      className="h-2 w-2 shrink-0 rounded-full border"
      style={{
        background: ok ? "var(--green-text)" : "transparent",
        borderColor: ok
          ? "var(--green-text)"
          : state === "failed"
            ? "var(--accent-text)"
            : "var(--muted)",
      }}
    />
  );
}

/** What each field starts with: the schema's suggestion, and never a stored value. */
function blankForm(entry: Integration, suggest: boolean): Record<string, string> {
  return Object.fromEntries(
    Object.entries(entry.schema.fields).map(([key, field]) => [
      key,
      // A `default` is a suggestion — a known URL, say — not something the
      // house is holding. Over a field that *is* configured it would be a
      // stored value replaced by a guess on the next save, so it is offered
      // only where there is nothing stored to overwrite.
      suggest && entry.configured[key] !== true ? field.default : "",
    ]),
  );
}

/**
 * One connected service: its name, what kind of thing it is, what the last
 * probe found, and — when the row is opened — the credentials it runs on.
 *
 * Its own module for `RoutineRow`'s reason: it is the repeated unit, it carries
 * a form and its own state, and `SystemSections.tsx` is four sections rather
 * than three sections and a form.
 *
 * The note under the name is `serviceNote` and nothing else. This row prints the
 * library's sentence verbatim and never a word of its own about state — the
 * vocabulary is closed, and a second place composing these sentences is a second
 * place for them to drift.
 */
export function IntegrationRow({ entry, row, save, onSave }: IntegrationRowProps) {
  const base = useId();
  const panelId = `${base}-panel`;
  const refusalId = `${base}-refusal`;

  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>(() => blankForm(entry, true));

  const { state, latency, status } = row ?? UNPROBED;
  const saving = save?.saving === true;
  const gated = save?.gated === true;
  // The gate's own sentence outranks the server's `403`: a reader off the home
  // network is being told to retype a password that was never the problem.
  const refusal = gated ? GATED : (save?.error ?? null);

  // The server has taken the credentials, so the form has nothing left to hold:
  // the placeholders below now read `saved`, and a token left sitting in a
  // mounted input is a secret with nobody watching it. Adjusted during render —
  // the pattern React documents for resetting state when an input changes —
  // and keyed on the confirmation stamp, so a *refused* save leaves the form
  // exactly as it was and nothing typed is lost.
  const savedAt = save?.savedAt ?? null;
  const [clearedFor, setClearedFor] = useState<number | null>(savedAt);
  if (clearedFor !== savedAt) {
    setClearedFor(savedAt);
    if (savedAt !== null) setValues(blankForm(entry, false));
  }

  const fields = Object.entries(entry.schema.fields);

  function submit() {
    // Only what was actually typed. An empty field is "leave this one alone" —
    // sending it would write an empty secret over a working one, and the route
    // stores field by field, so omitting it keeps what is there. A required
    // field left blank is refused by the server, which names it.
    const filled = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value.trim() !== ""),
    );
    onSave(entry.name, filled);
  }

  return (
    <div className="border-t border-line first:border-t-0">
      <button
        type="button"
        aria-expanded={open}
        // Only while open: `aria-controls` pointing at an absent id names nothing.
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((shown) => !shown)}
        className="flex min-h-14 w-full items-center justify-between gap-3 px-3 text-left"
      >
        <span className="flex min-w-0 flex-col gap-0.5 py-2">
          <span className="t-row truncate">{entry.name}</span>
          {/* Decorative: the name above it identifies the service and the note
              below carries everything load-bearing about it. */}
          <span className="t-meta">{`${entry.category} · ${entry.kind}`}</span>
          <span className="t-meta-strong">{serviceNote(state, status)}</span>
        </span>
        <span className="flex shrink-0 items-center gap-1.5">
          {/* A failed attempt has no round trip of its own, and the number
              react-query is still holding belongs to an earlier one — which is
              why `serviceRows` hands back null rather than the stale figure. */}
          {latency !== null && (
            <span className="t-meta-strong">{`${Math.round(latency)} ms`}</span>
          )}
          <span className="t-meta-strong" style={{ color: WORD_COLOUR[state] }}>
            {state}
          </span>
          <StateDot state={state} />
        </span>
      </button>

      {open && (
        <form
          id={panelId}
          onSubmit={(event) => {
            event.preventDefault();
            if (!saving) submit();
          }}
          className="flex flex-col gap-3 border-t border-line px-3 py-3"
        >
          {fields.map(([key, field]) => (
            // The help text sits beside the label, never inside it, so the
            // field's accessible name is exactly `field.label` — the same
            // arrangement the setup gate's credential step makes.
            <div key={key} className="flex flex-col gap-1.5">
              <label className="flex flex-col gap-1.5">
                <span className="t-label">{field.label}</span>
                <input
                  type={
                    field.field_type === "password"
                      ? "password"
                      : field.field_type === "url"
                        ? "url"
                        : "text"
                  }
                  // Never the stored value: `GET /api/integrations` sends
                  // `configured` and not the secrets, and a screen that echoed
                  // a password back would be inventing one.
                  value={values[key] ?? ""}
                  placeholder={entry.configured[key] === true ? "saved" : field.placeholder}
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-describedby={field.help_text ? `${base}-${key}-help` : undefined}
                  onChange={(event) =>
                    setValues((current) => ({ ...current, [key]: event.target.value }))
                  }
                  // The handoff asks for 14 px; iOS focus-zooms the whole layer
                  // under 16, and that rule wins. 48 px tall, radius 10, and a
                  // `--muted` edge rather than `--line`, which is 1.09:1 on
                  // this card and so is no boundary at all (WCAG 1.4.11).
                  className="h-12 rounded-[10px] border px-3 font-mono text-[16px]"
                  style={{
                    background: "var(--field)",
                    borderColor: "var(--muted)",
                    color: "var(--fg)",
                  }}
                />
              </label>
              {field.help_text && (
                <span id={`${base}-${key}-help`} className="t-meta">
                  {field.help_text}
                </span>
              )}
            </div>
          ))}

          {refusal !== null && (
            <span id={refusalId} className="t-meta-strong">
              {refusal}
            </span>
          )}

          {/* The bench's one filled button, because it is the one control here
              that writes a secret: `--ink` under `--paper`, which is the pairing
              the Memory bench's chosen sub-tab and Quiet's chosen chip already
              use. `aria-disabled` and a no-op rather than `disabled`, so the
              refusal above it can still be announced. */}
          <button
            type="submit"
            aria-disabled={saving ? true : undefined}
            aria-busy={saving ? true : undefined}
            aria-describedby={refusal === null ? undefined : refusalId}
            className="h-11 self-start rounded-[22px] border-0 px-[18px] text-[14px] font-medium"
            style={{ background: "var(--ink)", color: "var(--paper)" }}
          >
            {saving ? "Testing…" : "Save & test"}
          </button>
        </form>
      )}
    </div>
  );
}
