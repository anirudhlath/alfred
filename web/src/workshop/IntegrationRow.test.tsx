import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { serviceNote, type Integration, type ServiceRow } from "@/lib/system";
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

const row = (overrides: Partial<ServiceRow> = {}): ServiceRow => ({
  state: "ok",
  latency: 210,
  status: null,
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

describe("IntegrationRow", () => {
  it("says what the service is and what its last probe found", () => {
    draw();
    expect(screen.getByText("home-service")).toBeInTheDocument();
    expect(screen.getByText("service · service")).toBeInTheDocument();
    expect(screen.getByText("210 ms")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
    // The library's sentence, verbatim: a second place composing these is a
    // second place for them to drift.
    expect(screen.getByText(serviceNote("ok"))).toBeInTheDocument();
  });

  // Before `serviceRows` has seen the name there is a round trip in progress,
  // which is the truth — not an `unset` guessed from a missing entry.
  it("says a probe is in flight when it has no reading at all", () => {
    draw({ row: undefined });
    expect(screen.getByText("testing")).toBeInTheDocument();
    expect(screen.getByText("round-trip in progress · up to 10 s")).toBeInTheDocument();
  });

  it("keeps the credential form folded until the row is opened", () => {
    draw();
    const header = screen.getByRole("button", { expanded: false });
    expect(header).not.toHaveAttribute("aria-controls");
    expect(screen.queryByLabelText("Access Token")).not.toBeInTheDocument();

    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(header).toHaveAttribute("aria-controls", screen.getByLabelText("Access Token").closest("form")?.id ?? "");
  });

  it("builds one labelled field per schema entry, in the schema's own order", () => {
    open({ entry: FRESH });
    expect(screen.getAllByRole("textbox").length + screen.getAllByLabelText(/Token/).length).toBe(2);
    const labels = [...document.querySelectorAll("label > span")].map((s) => s.textContent);
    expect(labels).toEqual(["Home Assistant URL", "Access Token"]);
    expect(screen.getByText("Where the house answers")).toBeInTheDocument();
  });

  // The flag the schema really carries (`core/integrations/base.py`'s
  // `CredentialField.field_type`), not a guessed name for it.
  it("masks what the schema marks as a password and types the rest", () => {
    open({ entry: FRESH });
    expect(input("Access Token").type).toBe("password");
    expect(input("Home Assistant URL").type).toBe("url");
  });

  // `GET /api/integrations` sends `configured` and never the secrets, so a
  // screen that showed one would be inventing it.
  it("never puts a stored value in the field, and says one is stored instead", () => {
    open();
    for (const label of ["Home Assistant URL", "Access Token"]) {
      expect(input(label).value).toBe("");
      expect(input(label).placeholder).toBe("saved");
    }
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
  });

  it("sends what was typed, and leaves the fields nobody touched alone", () => {
    const { onSave } = open();
    fireEvent.change(input("Access Token"), { target: { value: "  llat-abc  " } });
    fireEvent.click(saveButton());
    // A blank field means "leave this one" — the route stores field by field,
    // so sending it empty would write an empty secret over a working one.
    expect(onSave).toHaveBeenCalledWith("home-service", { token: "  llat-abc  " });
  });

  it("saves on the keyboard's return as well as on the button", () => {
    const { onSave } = open();
    fireEvent.change(input("Access Token"), { target: { value: "llat-abc" } });
    fireEvent.submit(input("Access Token").closest("form") as HTMLFormElement);
    expect(onSave).toHaveBeenCalledWith("home-service", { token: "llat-abc" });
  });

  it("reads Testing… while it works, and refuses a second press", () => {
    const { onSave } = open({ save: save({ saving: true }), row: row({ state: "queued" }) });
    const button = saveButton();
    expect(button).toHaveTextContent("Testing…");
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAttribute("aria-busy", "true");
    // `aria-disabled` and a no-op, never `disabled`: a disabled control cannot
    // take focus, so the refusal it is described by is never announced.
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    expect(onSave).not.toHaveBeenCalled();
    // The state word is the hook's, and it is already saying the same thing.
    expect(screen.getByText("saved · testing")).toBeInTheDocument();
  });

  it("is the one filled control on the bench, because it is the one that writes a secret", () => {
    open();
    expect(saveButton()).toHaveStyle({ background: "var(--ink)", color: "var(--paper)" });
    expect(hasClass(saveButton(), "h-11")).toBe(true);
  });

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
    expect(saveButton().getAttribute("aria-describedby")).toBe(refusal.id);
  });

  it("quotes the server's own words for a refusal that is not the gate", () => {
    open({ save: save({ error: "422 · Missing required fields: ['url']" }) });
    expect(screen.getByText("422 · Missing required fields: ['url']")).toBeInTheDocument();
    expect(
      screen.queryByText("Credentials can only be changed from the home network."),
    ).not.toBeInTheDocument();
  });

  // Keyed on the confirmation stamp, so a refused save leaves everything where
  // it was and a taken one leaves no secret sitting in a mounted input.
  it("empties the form once the server has taken the credentials, and not before", () => {
    const { rerender, full } = open();
    fireEvent.change(input("Access Token"), { target: { value: "llat-abc" } });

    rerender(<IntegrationRow {...full} save={save({ saving: true })} />);
    expect(input("Access Token").value).toBe("llat-abc");

    rerender(<IntegrationRow {...full} save={save({ saving: true, savedAt: 1_700_000_000_000 })} />);
    expect(input("Access Token").value).toBe("");
  });

  it("prints the library's note for every state and never a word of its own", () => {
    const states: ServiceRow[] = [
      row({ state: "ok" }),
      row({ state: "unset", latency: null }),
      row({ state: "failed", latency: null, status: 404 }),
      row({ state: "failed", latency: null, status: null }),
    ];
    for (const state of states) {
      const { unmount } = draw({ row: state });
      expect(screen.getByText(serviceNote(state.state, state.status))).toBeInTheDocument();
      unmount();
    }
  });
});
