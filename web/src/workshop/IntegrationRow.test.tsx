import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  missingLabels,
  missingNote,
  SAVE_TOGETHER,
  serviceNote,
  STORED_PLACEHOLDER,
  TRANSIENT_NOTE,
  type Integration,
  type ServiceRow,
} from "@/lib/system";
import { integration } from "@/test/fixtures";
import { IntegrationRow } from "./IntegrationRow";
import type { CredentialSave } from "./useSystem";

/** Both fields stored: the common case, and the one that must never be echoed back. */
const HOME = integration();

/**
 * Nothing stored, and a schema that suggests a URL. A documentation address
 * (RFC 5737) rather than a real one — `alfred` is a public repository.
 */
const FRESH: Integration = integration({
  configured: { url: false, token: false },
  schema: {
    fields: {
      url: {
        label: "Home Assistant URL",
        field_type: "url",
        required: true,
        placeholder: "http://host:8123",
        default: "http://192.0.2.10:8123",
        help_text: "Where the house answers",
        transient: false,
      },
      token: {
        label: "Access Token",
        field_type: "password",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
    },
  },
});

/**
 * A stored field whose schema also carries a suggestion — the pairing the real
 * `home-service` entry has, and the one the never-pre-fill rule exists for.
 */
const STORED_WITH_DEFAULT: Integration = integration({
  configured: { url: true, token: true },
  schema: {
    ...FRESH.schema,
    fields: { ...FRESH.schema.fields },
  },
});

/**
 * Four fields of every kind the schema can declare, and a `configured` map that
 * mentions only one of them — `GET /api/integrations` reports what is stored,
 * not what the adapter asks for, so the form is built from the schema.
 */
const MIXED: Integration = integration({
  name: "robinhood",
  category: "broker",
  kind: "adapter",
  configured: { username: true },
  schema: {
    fields: {
      username: {
        label: "Username",
        field_type: "text",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
      password: {
        label: "Password",
        field_type: "password",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
      mfa_code: {
        label: "MFA code",
        field_type: "text",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: true,
      },
      note: {
        label: "Note",
        field_type: "text",
        required: false,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
    },
  },
});

const row = (overrides: Partial<ServiceRow> = {}): ServiceRow => ({
  state: "ok",
  status: null,
  detail: null,
  ...overrides,
});

const save = (overrides: Partial<CredentialSave> = {}): CredentialSave => ({
  saving: false,
  savedAt: null,
  error: null,
  gated: false,
  ...overrides,
});

const hasClass = (element: Element | null | undefined, name: string): boolean =>
  (element?.className ?? "").split(/\s+/).includes(name);

function draw(props: Partial<Parameters<typeof IntegrationRow>[0]> = {}) {
  const onSave = vi.fn();
  const full = { entry: HOME, row: row(), save: undefined, onSave, ...props };
  const rendered = render(<IntegrationRow {...full} />);
  return { ...rendered, onSave, full };
}

/** Open the row and hand back the form inside it. */
function open(props: Partial<Parameters<typeof IntegrationRow>[0]> = {}) {
  const drawn = draw(props);
  fireEvent.click(screen.getByRole("button", { expanded: false }));
  return drawn;
}

const saveButton = () => screen.getByRole("button", { name: /Save & test|Testing…/ });
const input = (label: string) => screen.getByLabelText(label) as HTMLInputElement;
const form = () => document.querySelector("form") as HTMLFormElement;
const describedBy = (element: Element): string[] =>
  (element.getAttribute("aria-describedby") ?? "").split(/\s+/).filter(Boolean);

/**
 * Fill every field the form will not send without.
 *
 * `entry` is checked against what was typed rather than merely used: every
 * caller goes on to assert about a form that has *let go*, and the route sends
 * all fields or none. A schema that grew a required field this helper did not
 * name would hold the save, and each of those assertions would then be about a
 * form that never submitted — which reads as a different failure, in a
 * different test, from the one that is really there. `missingLabels(entry, {})`
 * is every required label, because nothing is filled in an empty record.
 */
function fillRequired(entry: Integration, values: Record<string, string>) {
  for (const [label, value] of Object.entries(values)) {
    fireEvent.change(input(label), { target: { value } });
  }
  expect([...missingLabels(entry, {})].sort()).toEqual(Object.keys(values).sort());
}

describe("IntegrationRow", () => {
  it("says what the service is and what its last probe found", () => {
    draw();
    expect(screen.getByText("home-service")).toBeInTheDocument();
    expect(screen.getByText("service · service")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
    // The library's sentence, verbatim: a second place composing these is a
    // second place for them to drift.
    expect(screen.getByText(serviceNote("ok"))).toBeInTheDocument();
  });

  // The handoff puts latency in the Health grid and nowhere else, so `ServiceRow`
  // no longer carries one at all. This holds the *screen* to that: a round trip
  // beside the state word is a second reading of one probe, and while a save is
  // in flight it is a trip measured against the credentials being replaced.
  it("never prints a round trip beside the state word", () => {
    draw({ row: row({ state: "queued" }) });
    expect(screen.queryByText(/\bms\b/)).not.toBeInTheDocument();
  });

  // `--muted` is 3.20:1 on `--surface` in light, under AA. This line is the only
  // thing distinguishing `weather` the adapter from `weather` the service, so it
  // is not decoration.
  it("puts every line of the header above AA on the card", () => {
    draw();
    for (const text of ["service · service", serviceNote("ok"), "ok"]) {
      expect(hasClass(screen.getByText(text), "t-meta-strong")).toBe(true);
      expect(hasClass(screen.getByText(text), "t-meta")).toBe(false);
    }
  });

  it("gives the header a thumb's worth of height", () => {
    draw();
    expect(hasClass(screen.getByRole("button", { expanded: false }), "min-h-14")).toBe(true);
  });

  it("keeps the credential form folded until the row is opened, and folds it again", () => {
    draw();
    const header = screen.getByRole("button", { expanded: false });
    expect(header).not.toHaveAttribute("aria-controls");
    expect(screen.queryByLabelText("Access Token")).not.toBeInTheDocument();

    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "true");
    const panel = form();
    expect(panel.id).not.toBe("");
    expect(header.getAttribute("aria-controls")).toBe(panel.id);

    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Access Token")).not.toBeInTheDocument();
  });

  // Every row of the section renders the same markup, so a literal id would give
  // two rows the same `panel`, and the first header's `aria-controls` would
  // resolve to the second row's form.
  it("gives each row its own ids, so two rows never cross-wire", () => {
    const onSave = vi.fn();
    render(
      <>
        <IntegrationRow entry={HOME} row={row()} save={undefined} onSave={onSave} />
        <IntegrationRow entry={MIXED} row={row()} save={undefined} onSave={onSave} />
      </>,
    );
    const headers = screen.getAllByRole("button", { expanded: false });
    fireEvent.click(headers[0]);
    fireEvent.click(headers[1]);
    const forms = [...document.querySelectorAll("form")];
    expect(new Set(forms.map((f) => f.id)).size).toBe(2);
    expect(headers[0].getAttribute("aria-controls")).toBe(forms[0].id);
    expect(headers[1].getAttribute("aria-controls")).toBe(forms[1].id);
  });

  it("builds one labelled field per schema entry, in the schema's own order", () => {
    open({ entry: FRESH });
    const labels = [...document.querySelectorAll("label")].map((node) => node.textContent);
    expect(labels).toEqual(["Home Assistant URL", "Access Token"]);
    expect(input("Home Assistant URL")).toBeInstanceOf(HTMLInputElement);
    expect(input("Access Token")).toBeInstanceOf(HTMLInputElement);
  });

  // `configured` reports what is stored, not what the adapter asks for: a field
  // nobody has filled yet is absent from it, and a form built from it would have
  // no box to fill it in.
  it("builds the form from the schema and never from what is already stored", () => {
    open({ entry: MIXED });
    const labels = [...document.querySelectorAll("label")].map((node) => node.textContent);
    expect(labels).toEqual(["Username", "Password", "MFA code", "Note"]);
    expect(input("Password").placeholder).toBe("");
    expect(input("Username").placeholder).toBe(STORED_PLACEHOLDER);
  });

  // The flag the schema really carries (`core/integrations/base.py`'s
  // `CredentialField.field_type`), not a guessed name for it.
  it("masks what the schema marks as a password and types the rest", () => {
    open({ entry: FRESH });
    expect(input("Access Token").type).toBe("password");
    expect(input("Home Assistant URL").type).toBe("url");
  });

  // A password handed to a spellchecker or an autofill store is a password in a
  // third place nobody asked for.
  it("keeps the browser out of the credential fields", () => {
    open({ entry: FRESH });
    const field = input("Access Token");
    expect(field.getAttribute("autocomplete")).toBe("off");
    expect(field.getAttribute("spellcheck")).toBe("false");
    expect(field.getAttribute("autocapitalize")).toBe("none");
  });

  // `GET /api/integrations` sends `configured` and never the secrets, so a
  // screen that showed one would be inventing it — and the placeholder must not
  // imply the reader can leave it alone, because the route cannot take a partial
  // body (`validate_credential_body`).
  it("never puts a stored value in the field, and says one is stored instead", () => {
    open();
    for (const label of ["Home Assistant URL", "Access Token"]) {
      expect(input(label).value).toBe("");
      expect(input(label).placeholder).toBe(STORED_PLACEHOLDER);
    }
  });

  // The one rule protecting a stored secret from being overwritten by a guess.
  it("never offers the schema's suggestion over something already stored", () => {
    open({ entry: STORED_WITH_DEFAULT });
    expect(input("Home Assistant URL").value).toBe("");
    expect(input("Home Assistant URL").placeholder).toBe(STORED_PLACEHOLDER);
  });

  it("offers the schema's suggestion only where there is nothing to overwrite", () => {
    open({ entry: FRESH });
    // Nothing stored: the default is a help, not a value being replaced.
    expect(input("Home Assistant URL").value).toBe("http://192.0.2.10:8123");
    expect(input("Home Assistant URL").placeholder).toBe("http://host:8123");
    expect(input("Access Token").value).toBe("");
  });

  // 16 px, not the handoff's 14: below it iOS focus-zooms the whole layer, and
  // the zoom rule wins. `--muted` and not `--line`, which is 1.09:1 on a card.
  it("sizes the fields for a thumb and for a phone that will not zoom", () => {
    open();
    const field = input("Access Token");
    expect(hasClass(field, "h-12")).toBe(true);
    expect(hasClass(field, "text-[16px]")).toBe(true);
    expect(hasClass(field, "font-mono")).toBe(true);
    expect(hasClass(field, "rounded-[10px]")).toBe(true);
    expect(field.style.borderColor).toBe("var(--muted)");
    // Its own fill, so the box is visible as a box on the card it sits on.
    expect(field.style.background).toBe("var(--field)");
    expect(field.style.color).toBe("var(--fg)");
  });

  // `--muted` again: the visible label of a password field and the instructions
  // for filling it are the last two things on the bench that may be hard to read.
  it("puts the field's own label and help above AA", () => {
    open({ entry: FRESH });
    const label = screen.getByText("Home Assistant URL");
    expect(hasClass(label, "t-meta-strong")).toBe(true);
    expect(hasClass(label, "t-label")).toBe(false);
    expect(hasClass(screen.getByText(/Where the house answers/), "t-meta-strong")).toBe(true);
  });

  // The help text is the `aria-describedby` target; without the wiring it is
  // never announced, and the field's accessible name must stay the label alone.
  it("wires the help text to the field it explains", () => {
    open({ entry: FRESH });
    const field = input("Home Assistant URL");
    const help = document.getElementById(describedBy(field)[0]);
    expect(help?.textContent).toBe("Where the house answers");
    expect(describedBy(input("Access Token"))).toEqual([]);
  });

  // ── The route takes the whole body or nothing ──────────────────────────────

  it("says the form is all-or-nothing, in one standing sentence", () => {
    open();
    const note = screen.getByText(SAVE_TOGETHER);
    expect(hasClass(note, "t-meta-strong")).toBe(true);
    expect(describedBy(saveButton())).toContain(note.id);
  });

  it("marks every required field the reader has not filled, and holds the save", () => {
    const { onSave } = open();
    expect(screen.getAllByText("needed")).toHaveLength(2);
    const button = saveButton();
    expect(button).toHaveAttribute("aria-disabled", "true");
    // Never `disabled`: a disabled control cannot take focus, so the sentence
    // saying why is never announced.
    expect(button).not.toBeDisabled();

    const blank = screen.getByText(
      missingNote(["Home Assistant URL", "Access Token"]),
    );
    expect(describedBy(button)).toContain(blank.id);

    button.focus();
    fireEvent.click(button);
    expect(onSave).not.toHaveBeenCalled();
    // And the focus is still here to hear it. `not.toBeDisabled()` above is
    // only half the claim: a handler that blurred itself on a refused press
    // would throw the reader to `<body>` and take the note with it.
    expect(document.activeElement).toBe(button);
  });

  it("drops each mark as its field is filled, and lets go once none is left", () => {
    const { onSave } = open();
    fireEvent.change(input("Home Assistant URL"), { target: { value: "http://192.0.2.10:8123" } });
    expect(screen.getAllByText("needed")).toHaveLength(1);
    expect(screen.getByText(missingNote(["Access Token"]))).toBeInTheDocument();

    fireEvent.change(input("Access Token"), { target: { value: "llat-abc" } });
    expect(screen.queryByText("needed")).not.toBeInTheDocument();
    expect(screen.queryByText(/Still blank/)).not.toBeInTheDocument();
    expect(saveButton()).not.toHaveAttribute("aria-disabled");

    fireEvent.click(saveButton());
    expect(onSave).toHaveBeenCalledWith("home-service", {
      url: "http://192.0.2.10:8123",
      token: "llat-abc",
    });
  });

  // A blank required field is a 422 the reader cannot see coming; the rule has
  // to be on the field as well as in the sentence.
  it("puts the rule on the field itself", () => {
    open({ entry: MIXED });
    for (const label of ["Username", "Password"]) {
      expect(input(label).required).toBe(true);
      expect(input(label).getAttribute("aria-required")).toBe("true");
    }
    // Declared `required` but transient, so the keyring never holds it and the
    // form must not insist on it.
    expect(input("MFA code").required).toBe(false);
    expect(input("Note").required).toBe(false);
  });

  // The client's own named refusal is the one worth reading; the browser's
  // bubble would arrive first and say less.
  it("keeps the browser's validation bubble out of the way", () => {
    open();
    expect(form().noValidate).toBe(true);
  });

  // A transient field is never persisted, so `configured` is false for it for
  // ever and "blank means leave this one" would silently drop it.
  it("says which fields the house will not keep", () => {
    open({ entry: MIXED });
    const note = screen.getByText(new RegExp(TRANSIENT_NOTE));
    expect(describedBy(input("MFA code"))).toContain(note.id);
    expect(input("Note").getAttribute("aria-describedby")).toBeNull();
  });

  it("sends every field that has something in it, and nothing that has not", () => {
    const { onSave } = open({ entry: MIXED });
    fillRequired(MIXED, { Username: "ada", Password: "hunter2" });
    // Whitespace is not a credential.
    fireEvent.change(input("Note"), { target: { value: "   " } });
    fireEvent.click(saveButton());
    expect(onSave).toHaveBeenCalledWith("robinhood", { username: "ada", password: "hunter2" });
  });

  it("saves on the keyboard's return as well as on the button", () => {
    const { onSave } = open();
    fillRequired(HOME, { "Home Assistant URL": "http://192.0.2.10:8123", "Access Token": "llat" });
    // `false` is `preventDefault` having been called: without it the form
    // navigates and the whole app is gone.
    expect(fireEvent.submit(form())).toBe(false);
    expect(onSave).toHaveBeenCalledWith("home-service", {
      url: "http://192.0.2.10:8123",
      token: "llat",
    });
  });

  it("reads Testing… while it works, and refuses a second press", () => {
    const { onSave } = open({ save: save({ saving: true }), row: row({ state: "queued" }) });
    fillRequired(HOME, { "Home Assistant URL": "http://192.0.2.10:8123", "Access Token": "llat" });
    const button = saveButton();
    expect(button).toHaveTextContent("Testing…");
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).not.toBeDisabled();
    button.focus();
    fireEvent.click(button);
    expect(onSave).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(button);
    // The state word is the hook's, and it is already saying the same thing.
    expect(screen.getByText("saved · testing")).toBeInTheDocument();
  });

  // `saving` is a live flag, not "this row has a history": a row that saved an
  // hour ago is not busy.
  it("goes back to Save & test once the save has settled", () => {
    open({ save: save({ savedAt: 1_700_000_000_000 }) });
    expect(saveButton()).toHaveTextContent("Save & test");
    expect(saveButton()).not.toHaveAttribute("aria-busy");
  });

  it("is the one filled control on the bench, because it is the one that writes a secret", () => {
    open();
    expect(saveButton()).toHaveStyle({ background: "var(--ink)", color: "var(--paper)" });
    expect(hasClass(saveButton(), "h-11")).toBe(true);
  });

  // ── Refusals ───────────────────────────────────────────────────────────────

  // Off the home network every read on this bench works and this write alone
  // 403s, so the row has to say which of the two gates refused it.
  it("names the gate on a 403, and keeps the form filled", () => {
    const { rerender, full } = open();
    fireEvent.change(input("Access Token"), { target: { value: "llat-abc" } });
    rerender(<IntegrationRow {...full} save={save({ error: "403", gated: true })} />);

    const refusal = screen.getByText("Credentials can only be changed from the home network.");
    expect(hasClass(refusal, "t-meta-strong")).toBe(true);
    // Nothing typed is lost — the reader is not retyping a password that was
    // never the problem.
    expect(input("Access Token").value).toBe("llat-abc");
    expect(describedBy(saveButton())).toContain(refusal.id);
  });

  // `saves[name]` survives a trip to another bench; a sentence that only existed
  // inside an open form would not, and the header would go on reading `ok`.
  it("shows a refusal whether or not the row is open", () => {
    draw({ save: save({ error: "403", gated: true }) });
    expect(screen.queryByLabelText("Access Token")).not.toBeInTheDocument();
    expect(
      screen.getByText("Credentials can only be changed from the home network."),
    ).toBeInTheDocument();
  });

  it("quotes the server's own words for a refusal that is not the gate", () => {
    draw({ save: save({ error: "422 · Missing required fields: ['url']" }) });
    expect(screen.getByText("422 · Missing required fields: ['url']")).toBeInTheDocument();
    expect(
      screen.queryByText("Credentials can only be changed from the home network."),
    ).not.toBeInTheDocument();
  });

  // ── What happens to what was typed ─────────────────────────────────────────

  // Keyed on the confirmation stamp, so a refused save leaves everything where
  // it was and a taken one leaves no secret sitting in a mounted input.
  it("empties the form once the server has taken the credentials, and not before", () => {
    const { rerender, full } = open({ entry: FRESH });
    fireEvent.change(input("Access Token"), { target: { value: "llat-abc" } });

    rerender(<IntegrationRow {...full} save={save({ saving: true })} />);
    expect(input("Access Token").value).toBe("llat-abc");

    rerender(<IntegrationRow {...full} save={save({ saving: true, savedAt: 1_700_000_000_000 })} />);
    expect(input("Access Token").value).toBe("");
    // And not back to the suggestion: `configured` has not caught up, so
    // re-offering a default over a value the house has just taken would put a
    // guess where the stored value now is.
    expect(input("Home Assistant URL").value).toBe("");
  });

  // A server upgrade can add a field to an adapter while this row is open. The
  // form is built from the schema on every render, so the new box appears — and
  // without a reset it would be a box with nothing behind it, never offering its
  // own default, while the retired key went on being sent.
  it("starts again when the schema itself changes under the row", () => {
    const { rerender, full } = open({ entry: MIXED });
    fireEvent.change(input("Username"), { target: { value: "ada" } });

    const grown: Integration = {
      ...MIXED,
      schema: { fields: { ...MIXED.schema.fields, ...FRESH.schema.fields } },
    };
    rerender(<IntegrationRow {...full} entry={grown} />);

    expect(input("Home Assistant URL").value).toBe("http://192.0.2.10:8123");
    // And the whole form with it: a value typed against the old shape is not a
    // value for the new one.
    expect(input("Username").value).toBe("");
  });

  // Folding the row is walking away from it. The value lives in React state for
  // the life of the bench otherwise — invisible, unclearable, and a second open
  // row is a second live secret-bearing form.
  it("forgets what was typed when the row is folded away", () => {
    open({ entry: FRESH });
    fireEvent.change(input("Access Token"), { target: { value: "llat-abc" } });
    fireEvent.click(screen.getByRole("button", { expanded: true }));
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(input("Access Token").value).toBe("");
    // Back to the state the row was born in, suggestions included.
    expect(input("Home Assistant URL").value).toBe("http://192.0.2.10:8123");
  });

  // ── The state word and its dot ─────────────────────────────────────────────

  it("prints the library's note for every state and never a word of its own", () => {
    const states: ServiceRow[] = [
      row({ state: "ok" }),
      row({ state: "unset" }),
      row({ state: "failed", status: 404 }),
      row({ state: "failed", status: null }),
      row({ state: "failed", status: null, detail: "connection refused" }),
    ];
    for (const state of states) {
      const { unmount } = draw({ row: state });
      expect(
        screen.getByText(serviceNote(state.state, state.status, state.detail)),
      ).toBeInTheDocument();
      unmount();
    }
  });

  // The service answered 200 and said it was sick; the row must pass on what it
  // said rather than accuse it of a status it never sent.
  it("carries the service's own reason through to the note", () => {
    draw({ row: row({ state: "failed", status: null, detail: "connection refused" }) });
    expect(screen.getByText(/^connection refused · /)).toBeInTheDocument();
  });

  // Fill versus outline, not hue: `--green` against `--muted` is 1.22:1 dark.
  // A dot that filled for `testing` would claim health it has not got.
  it("fills the dot only for a service that is answering", () => {
    const seen: Record<string, { background: string; borderColor: string }> = {};
    for (const state of ["ok", "failed", "unset", "testing", "queued"] as const) {
      const { unmount } = draw({ row: row({ state }) });
      const dot = screen.getByTestId("service-dot");
      seen[state] = { background: dot.style.background, borderColor: dot.style.borderColor };
      expect(dot).toHaveAttribute("aria-hidden", "true");
      unmount();
    }
    expect(seen.ok).toEqual({ background: "var(--green-text)", borderColor: "var(--green-text)" });
    for (const state of ["failed", "unset", "testing", "queued"] as const) {
      expect(seen[state].background).toBe("transparent");
    }
    expect(seen.failed.borderColor).toBe("var(--accent-text)");
    // Never `--line`, which is 1.09:1 on this card and so is no ring at all.
    expect(seen.unset.borderColor).toBe("var(--muted)");
  });

  it("colours the state word without spending the card's one accent on a busy row", () => {
    for (const [state, colour] of [
      ["ok", "var(--green-text)"],
      ["failed", "var(--accent-text)"],
      ["unset", "var(--fg2)"],
      ["testing", "var(--fg2)"],
      ["queued", "var(--fg2)"],
    ] as const) {
      const { unmount } = draw({ row: row({ state }) });
      expect(within(screen.getByRole("button")).getByText(state).style.color).toBe(colour);
      unmount();
    }
  });
});
