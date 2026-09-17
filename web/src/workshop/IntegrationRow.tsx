import { useId, useState } from "react";
import {
  missingLabels,
  missingNote,
  requiredFields,
  SAVE_TOGETHER,
  serviceNote,
  STORED_PLACEHOLDER,
  TRANSIENT_NOTE,
  type Integration,
  type ServiceRow,
  type ServiceState,
} from "@/lib/system";
import type { CredentialSave } from "./useSystem";

export interface IntegrationRowProps {
  entry: Integration;
  /** What the last probe found. `serviceRows` keys every entry of the same list. */
  row: ServiceRow;
  /** This client's last credential save for this name, if it has made one. */
  save: CredentialSave | undefined;
  onSave: (name: string, values: Record<string, string>) => void;
}

/**
 * The colour each state word wears. `--fg2` and **not** the handoff's `--muted`
 * for `unset`: `--muted` is 3.20:1 on `--surface` in light, under AA, and this
 * word is the entire state of the row rather than decoration beside it
 * (`test/contrast.test.ts` measures both).
 *
 * Three of the five share `--fg2`, and that is the point rather than an
 * oversight: only `ok` and `failed` are verdicts. `unset`, `testing` and
 * `queued` are a keyring that is empty, a probe in flight and this client's own
 * claim that a save landed, and spending the card's one "look here" colour on a
 * row that is merely busy would blunt it on the row that is broken.
 */
const WORD_COLOUR: Record<ServiceState, string> = {
  ok: "var(--green-text)",
  failed: "var(--accent-text)",
  unset: "var(--fg2)",
  testing: "var(--fg2)",
  queued: "var(--fg2)",
};

/**
 * What a refusal from the trusted-network gate means, in the words the reader
 * needs.
 *
 * `api` emits `denied` for every 403, so the Denied gate rises over this too,
 * and both are kept deliberately. `gates/AuthGate.tsx:101-103` dismisses with
 * `onDismiss={() => setDenied(false)}`, which closes that `<Layer>` and nothing
 * else: the bench, this row and everything typed into it are exactly as they
 * were underneath. So the sentence is what the reader lands back on. The gate
 * names the network; the sentence names the row, which the gate cannot, and
 * keeps the typed values in front of it. (Reviewed twice; this note is here so
 * it is not re-litigated a third time.)
 */
const GATED = "Credentials can only be changed from the home network.";

/** The mark on a required field that is still empty. */
const NEEDED = "needed";

/**
 * The dot beside the state word: **filled when the service is answering and an
 * outline when it is not.** Fill versus outline rather than hue, because
 * `--green` against `--muted` is 1.22:1 dark and 1.51:1 light — two circles of
 * near-identical luminance, and a reader who cannot separate those hues would
 * have nothing at all.
 *
 * The outline's colour is a second, redundant channel and nothing more: only
 * `failed` earns the accent ring, and the three states that are neither verdict
 * share `--muted`, exactly as `WORD_COLOUR` shares `--fg2` for them. The word
 * beside the dot is what actually distinguishes the five, and the dot stays
 * `aria-hidden` because of it.
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
      // A `default` is a suggestion — `apple_calendar`'s `caldav_url` is
      // iCloud's — not something the house is holding. Over a field that *is*
      // configured it would be a stored value replaced by a guess on the next
      // save, so it is offered only where there is nothing to overwrite.
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
 *
 * **The form asks for every field, every time.** `PUT …/credentials` validates
 * before it stores and 422s on any required field absent from the body
 * (`core/channels/service_credentials.py`, `validate_credential_body`), so
 * there is no partial write to offer: sending only what changed makes every
 * multi-field service unrotatable, and sending blanks for the rest would write
 * empty secrets over working ones. The form says so, marks what is still empty,
 * and refuses before the request rather than letting the house refuse after it.
 */
export function IntegrationRow({ entry, row, save, onSave }: IntegrationRowProps) {
  const base = useId();
  const panelId = `${base}-panel`;
  const refusalId = `${base}-refusal`;
  const blankId = `${base}-blank`;
  const togetherId = `${base}-together`;

  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>(() => blankForm(entry, true));

  const { state, status, detail } = row;
  const saving = save?.saving === true;
  const gated = save?.gated === true;
  // The gate's own sentence outranks the server's `403`: a reader off the home
  // network is being told to retype a password that was never the problem.
  const refusal = gated ? GATED : (save?.error ?? null);

  const fields = Object.entries(entry.schema.fields);
  const savedAt = save?.savedAt ?? null;
  const shape = fields.map(([key]) => key).join("\u0000");
  const [clearedFor, setClearedFor] = useState<number | null>(savedAt);
  const [shapedFor, setShapedFor] = useState(shape);

  // Two reasons the form has to go back to its starting state, both adjusted
  // during render — the pattern React documents for resetting state when an
  // input changes — and both settled here, before anything below reads a value,
  // so `form` holds a string for every field on screen and nothing downstream
  // needs a fallback for a key that is not there.
  let form = values;

  // One: the schema changed under a mounted row. The list is re-read after
  // every save and a server upgrade can add a field to an adapter while this
  // row is open; stale `values` would leave the new box with nothing behind it,
  // never offering its default, while the retired key went on being sent.
  if (shapedFor !== shape) {
    setShapedFor(shape);
    form = blankForm(entry, true);
    setValues(form);
  }

  // Two: the server has taken the credentials, so the form has nothing left to
  // hold — the boxes now say something is stored, and a token left sitting in a
  // mounted input is a secret with nobody watching it. Keyed on the
  // confirmation stamp, so a *refused* save leaves the form exactly as it was
  // and nothing typed is lost. No `suggest` here: `configured` has not caught up
  // yet, and re-offering a default over a value the house has just taken is the
  // one thing `blankForm` exists to prevent.
  if (clearedFor !== savedAt) {
    setClearedFor(savedAt);
    if (savedAt !== null) {
      form = blankForm(entry, false);
      setValues(form);
    }
  }

  const needed = new Set(requiredFields(entry));
  const missing = missingLabels(entry, form);
  // Held, not pressed and then refused: the reader is told which box is empty
  // beside the box, rather than by a 422 naming a field whose own placeholder
  // said it was saved.
  const held = saving || missing.length > 0;

  function close() {
    setOpen(false);
    // Folding the row is walking away from it. Back to the state the row was
    // born in — the schema's suggestions and nothing typed — so no secret
    // survives out of sight for the life of the bench.
    setValues(blankForm(entry, true));
  }

  return (
    <div className="border-t border-line first:border-t-0">
      <button
        type="button"
        aria-expanded={open}
        // Only while open: `aria-controls` pointing at an absent id names nothing.
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? close() : setOpen(true))}
        className="flex min-h-14 w-full items-center justify-between gap-3 px-3 text-left"
      >
        <span className="flex min-w-0 flex-col gap-0.5 py-2">
          <span className="t-row truncate">{entry.name}</span>
          {/* `.t-meta-strong`, not `.t-meta`: this line is the only thing
              telling `weather` the adapter from `weather` the registry
              service, and `--muted` is 3.20:1 on this card in light. */}
          <span className="t-meta-strong">{`${entry.category} · ${entry.kind}`}</span>
          <span className="t-meta-strong">{serviceNote(state, status, detail)}</span>
        </span>
        <span className="flex shrink-0 items-center gap-1.5">
          <span className="t-meta-strong" style={{ color: WORD_COLOUR[state] }}>
            {state}
          </span>
          <StateDot state={state} />
        </span>
      </button>

      {/* Outside the panel, deliberately: a refusal is news about the row
          whether or not the reader has it open, and `TriggerRow` puts its own
          outside for the same reason. `saves[name]` survives a trip to another
          bench; a sentence that only existed inside an open form would not. */}
      {refusal !== null && (
        <p id={refusalId} className="t-meta-strong m-0 px-3 pb-2.5">
          {refusal}
        </p>
      )}

      {open && (
        <form
          id={panelId}
          // The client's own named refusal is the one the reader should see;
          // the browser's bubble would arrive first and say less.
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            if (held) return;
            // Every field that has something in it. With the save held until
            // none is blank, that is every required field — and a transient or
            // optional one only when it was actually typed.
            onSave(
              entry.name,
              Object.fromEntries(Object.entries(form).filter(([, v]) => v.trim() !== "")),
            );
          }}
          className="flex flex-col gap-3 border-t border-line px-3 py-3"
        >
          <span id={togetherId} className="t-meta-strong">
            {SAVE_TOGETHER}
          </span>

          {fields.map(([key, field]) => {
            const fieldId = `${base}-${key}`;
            const helpId = `${fieldId}-help`;
            const required = needed.has(key);
            // A transient field is never persisted, so `configured` is false
            // for it for ever and its box must not claim otherwise.
            const notes = [
              field.transient ? TRANSIENT_NOTE : null,
              field.help_text === "" ? null : field.help_text,
            ].filter((note): note is string => note !== null);
            return (
              <div key={key} className="flex flex-col gap-1.5">
                {/* The mark sits beside the label and not inside it, so the
                    field's accessible name stays exactly `field.label` — the
                    same arrangement the setup gate's credential step makes with
                    its help text. */}
                <div className="flex items-baseline justify-between gap-2">
                  {/* `.t-meta-strong` in caps rather than `.t-label`, which
                      carries `--muted`: `SystemFrame` made the same swap for
                      the section headings, and this is the visible label of a
                      password field on the harder surround. */}
                  <label
                    htmlFor={fieldId}
                    className="t-meta-strong uppercase"
                    style={{ letterSpacing: "0.08em" }}
                  >
                    {field.label}
                  </label>
                  {required && form[key].trim() === "" && (
                    <span className="t-meta-strong">{NEEDED}</span>
                  )}
                </div>
                <input
                  id={fieldId}
                  type={
                    field.field_type === "password"
                      ? "password"
                      : field.field_type === "url"
                        ? "url"
                        : "text"
                  }
                  required={required}
                  aria-required={required || undefined}
                  // Never the stored value: `GET /api/integrations` sends
                  // `configured` and not the secrets, and a screen that echoed
                  // a password back would be inventing one.
                  value={form[key]}
                  placeholder={
                    entry.configured[key] === true ? STORED_PLACEHOLDER : field.placeholder
                  }
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-describedby={notes.length > 0 ? helpId : undefined}
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
                {notes.length > 0 && (
                  <span id={helpId} className="t-meta-strong">
                    {notes.join(" · ")}
                  </span>
                )}
              </div>
            );
          })}

          {missing.length > 0 && (
            <span id={blankId} className="t-meta-strong">
              {missingNote(missing)}
            </span>
          )}

          {/* The bench's one filled button, because it is the one control here
              that writes a secret: `--ink` under `--paper`, which is the pairing
              the Memory bench's chosen sub-tab and Quiet's chosen chip already
              use. `aria-disabled` and a no-op rather than `disabled`, so the
              notes it is described by can still be announced. */}
          <button
            type="submit"
            aria-disabled={held ? true : undefined}
            aria-busy={saving ? true : undefined}
            aria-describedby={[
              togetherId,
              missing.length > 0 ? blankId : null,
              refusal !== null ? refusalId : null,
            ]
              .filter((id): id is string => id !== null)
              .join(" ")}
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
