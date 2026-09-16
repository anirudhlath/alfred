import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { credentialMeta, serviceNote, sessionMeta, type ServiceRow } from "@/lib/system";
import { authSession, credential, integration, SYSTEM_NOW } from "@/test/fixtures";
import {
  IdentitySection,
  ReflexSection,
  ServicesSection,
  SessionsSection,
} from "./SystemSections";
import type { Attention, Credentials, Integrations, Pairing, Sessions } from "./useSystem";

/**
 * 21:15 the same evening the System fixtures were written — when the server
 * confirmed an end, which is never when the button was pressed.
 */
const ENDED_AT = new Date(2026, 8, 16, 21, 15, 0).getTime();
/** 21:20, five minutes after a code is minted: the pairing window's close. */
const PAIRING_CLOSES = new Date(2026, 8, 16, 21, 20, 0).toISOString();

const PHONE = authSession({ session_id: "sess-phone", device_name: "Phone", current: true });
const LAPTOP = authSession({
  session_id: "sess-laptop",
  credential_id: "cred-laptop",
  device_name: "Laptop",
  // A documentation address (RFC 5737), never a real one — `alfred` is public.
  ip: "192.0.2.31",
  current: false,
});

const HOME = integration();
const WEATHER = integration({
  name: "weather",
  category: "weather",
  kind: "adapter",
  schema: {
    fields: {
      api_key: {
        label: "API key",
        field_type: "password",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
    },
  },
  configured: { api_key: false },
});

const ATTENTION_INTRO =
  "Alfred acts on these without asking. Everything else it asks about first.";

function sessions(overrides: Partial<Sessions> = {}): Sessions {
  return {
    list: [PHONE, LAPTOP],
    read: true,
    ended: {},
    ending: {},
    failed: {},
    end: vi.fn(),
    error: null,
    ...overrides,
  };
}

const row = (overrides: Partial<ServiceRow> = {}): ServiceRow => ({
  state: "ok",
  status: null,
  detail: null,
  ...overrides,
});

function integrations(overrides: Partial<Integrations> = {}): Integrations {
  return {
    list: [HOME, WEATHER],
    read: true,
    rows: { "home-service": row(), weather: row({ state: "unset" }) },
    saves: {},
    save: vi.fn(),
    error: null,
    ...overrides,
  };
}

function credentials(overrides: Partial<Credentials> = {}): Credentials {
  return {
    list: [credential({ current: true })],
    read: true,
    signOut: vi.fn(),
    signingOut: false,
    error: null,
    ...overrides,
  };
}

function pairing(overrides: Partial<Pairing> = {}): Pairing {
  return { code: null, expiresAt: null, minting: false, error: null, mint: vi.fn(), ...overrides };
}

function attention(overrides: Partial<Attention> = {}): Attention {
  return {
    domains: [
      { domain: "light", members: ["light.hall"], seen: ["light.hall", "light.study"] },
      { domain: "lock", members: [], seen: ["lock.front"] },
    ],
    read: true,
    saving: {},
    failed: {},
    allow: vi.fn(),
    ask: vi.fn(),
    error: null,
    ...overrides,
  };
}

/** One whole class, never a substring: `h-11` must not be matched by `h-110`. */
const hasClass = (element: Element | null | undefined, name: string): boolean =>
  (element?.className ?? "").split(/\s+/).includes(name);

/** 44 px of touch target, however the element reaches it. */
const tall = (element: Element): boolean =>
  ["h-11", "min-h-11", "min-h-14"].some((name) => hasClass(element, name));

/** The 56 px row a piece of text sits in — `SystemFrame`'s own height class. */
const rowOf = (text: string): HTMLElement => {
  const found = screen.getByText(text).closest<HTMLElement>(".min-h-14");
  if (found === null) throw new Error(`"${text}" is not inside a row`);
  return found;
};

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

describe("SessionsSection", () => {
  it("names every session and what opened it", () => {
    render(<SessionsSection sessions={sessions()} now={SYSTEM_NOW} />);
    expect(screen.getByRole("heading", { name: "Sessions" })).toBeInTheDocument();
    expect(screen.getByText("Phone")).toBeInTheDocument();
    expect(screen.getByText("Laptop")).toBeInTheDocument();
    // The formatter's line, not one composed here: two rows on one screen built
    // by two different rules is the split `lib/system.ts` exists to prevent.
    expect(
      screen.getByText("passkey · web · this device · signed in 07:02 · 192.168.1.24"),
    ).toBeInTheDocument();
    expect(screen.getByText(sessionMeta(LAPTOP, SYSTEM_NOW))).toBeInTheDocument();
  });

  it("writes the meta a reader has to trust in the type that carries it", () => {
    render(<SessionsSection sessions={sessions()} now={SYSTEM_NOW} />);
    const meta = screen.getByText(sessionMeta(LAPTOP, SYSTEM_NOW));
    expect(hasClass(meta, "t-meta-strong")).toBe(true);
    expect(hasClass(screen.getByText("Laptop"), "t-row")).toBe(true);
  });

  // You do not "end" the session you are reading the list on: the row says so
  // and offers nothing to press.
  it("labels your own session rather than offering to end it", () => {
    render(<SessionsSection sessions={sessions()} now={SYSTEM_NOW} />);
    expect(screen.getByText("current")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /current/ })).not.toBeInTheDocument();
    expect(within(rowOf("Phone")).queryByRole("button")).not.toBeInTheDocument();
    const end = screen.getByRole("button", { name: /^End Laptop$/ });
    // The `aria-label` carries the device name so four rows do not all announce
    // the same word; the visible control still has to read `End`.
    expect(end.textContent).toBe("End");
    expect(end).toHaveStyle({ color: "var(--accent-text)" });
    expect(tall(end)).toBe(true);
  });

  it("ends the row that was pressed, by its own id", () => {
    const state = sessions();
    render(<SessionsSection sessions={state} now={SYSTEM_NOW} />);
    fireEvent.click(screen.getByRole("button", { name: /^End Laptop$/ }));
    expect(state.end).toHaveBeenCalledWith("sess-laptop");
  });

  // The server's confirmation time, and the vocabulary's own word for a write
  // that landed. Never the moment the button was pressed.
  it("reports an ended row as applied, at the moment the server confirmed it", () => {
    render(
      <SessionsSection
        sessions={sessions({ ended: { "sess-laptop": ENDED_AT } })}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.getByText("ended 21:15 · applied")).toBeInTheDocument();
    // It cannot be ended twice, so there is nothing left to press.
    expect(screen.queryByRole("button", { name: /^End Laptop$/ })).not.toBeInTheDocument();
  });

  // By token, never by opacity: a whole-element alpha composites every layer
  // under it and is invisible to `src/test/contrast.ts`, which is why
  // `TriggerRow` steps a spent one-shot back the same way.
  it("recedes an ended row by swapping tokens, not by fading it", () => {
    render(
      <SessionsSection
        sessions={sessions({ ended: { "sess-laptop": ENDED_AT } })}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.getByText("Laptop")).toHaveStyle({ color: "var(--fg2)" });
    // The row it is still standing in, too — and nothing anywhere carries one.
    const ended = rowOf("Laptop");
    expect(ended.style.opacity).toBe("");
    for (const node of ended.querySelectorAll<HTMLElement>("*")) {
      expect(node.style.opacity).toBe("");
    }
    // The row that was not ended keeps its own colour.
    expect(screen.getByText("Phone").style.color).toBe("");
  });

  it("keeps the ended mark on the row it belongs to", () => {
    render(
      <SessionsSection
        sessions={sessions({ ended: { "sess-laptop": ENDED_AT } })}
        now={SYSTEM_NOW}
      />,
    );
    expect(within(rowOf("Laptop")).getByText("ended 21:15 · applied")).toBeInTheDocument();
    expect(within(rowOf("Phone")).queryByText(/ended/)).not.toBeInTheDocument();
  });

  // `aria-disabled` and a no-op, never `disabled`: a disabled control cannot
  // take focus, so the note describing what it just did is never announced.
  it("refuses a second press while the first is still in flight", () => {
    const state = sessions({ ending: { "sess-laptop": true } });
    render(<SessionsSection sessions={state} now={SYSTEM_NOW} />);
    const end = screen.getByRole("button", { name: /^End Laptop$/ });
    expect(end).toHaveAttribute("aria-disabled", "true");
    expect(end).toHaveAttribute("aria-busy", "true");
    expect(end).not.toBeDisabled();
    fireEvent.click(end);
    expect(state.end).not.toHaveBeenCalled();
  });

  it("says so when there is nothing to list", () => {
    render(<SessionsSection sessions={sessions({ list: [] })} now={SYSTEM_NOW} />);
    expect(screen.getByText("No other sessions.")).toBeInTheDocument();
  });

  // A signed-in reader always has at least their own session, so this sentence
  // is only ever shown when it is false: before the first answer the list is
  // empty because nothing has replied.
  it("says nothing at all until a read has landed", () => {
    render(<SessionsSection sessions={sessions({ list: [], read: false })} now={SYSTEM_NOW} />);
    expect(screen.queryByText("No other sessions.")).not.toBeInTheDocument();
  });

  it("does not call a list it could not read an empty one", () => {
    render(
      <SessionsSection
        sessions={sessions({ list: [], error: "Session store unavailable" })}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.getByText("Session store unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No other sessions.")).not.toBeInTheDocument();
  });

  // `endFailed` is keyed exactly as `ending` is: a second row's attempt must not
  // erase the first row's refusal, and `errorText` carries no device name.
  it("puts a refused end on the row that was refused", () => {
    render(
      <SessionsSection
        sessions={sessions({ failed: { "sess-laptop": "503 · Session store unavailable" } })}
        now={SYSTEM_NOW}
      />,
    );
    const refusal = within(rowOf("Laptop")).getByText("503 · Session store unavailable");
    expect(hasClass(refusal, "t-meta-strong")).toBe(true);
    expect(within(rowOf("Phone")).queryByText(/503/)).not.toBeInTheDocument();
    // It failed, so the session is still live and still endable.
    expect(screen.getByRole("button", { name: /^End Laptop$/ })).toBeInTheDocument();
  });

  // Two laptops on one account share a device name. Keyed on it they would be
  // one React child, and the ended stamp would land on whichever won.
  it("keys a row on its session id and not on the device's name", () => {
    const warn = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <SessionsSection
        sessions={sessions({
          list: [
            authSession({ session_id: "sess-a", device_name: "Laptop" }),
            authSession({ session_id: "sess-b", device_name: "Laptop" }),
          ],
          ended: { "sess-b": ENDED_AT },
        })}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.getAllByText("Laptop")).toHaveLength(2);
    expect(screen.getAllByText("ended 21:15 · applied")).toHaveLength(1);
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  // `SectionNote` returns nothing when there is nothing to say; a bordered strip
  // of empty card is a fact the reader has to work out is not one.
  it("leaves no empty note behind when nothing failed", () => {
    render(<SessionsSection sessions={sessions()} now={SYSTEM_NOW} />);
    expect(document.querySelectorAll("p")).toHaveLength(0);
  });

  it("carries its own read failure rather than taking the bench down", () => {
    render(
      <SessionsSection sessions={sessions({ error: "Session store unavailable" })} now={SYSTEM_NOW} />,
    );
    expect(screen.getByText("Session store unavailable")).toBeInTheDocument();
    // The rows stay underneath holding what they were last told (spec §5.2).
    expect(screen.getByText("Laptop")).toBeInTheDocument();
  });

  // `_expires_in` maps a missing key, a persistent key and any non-numeric all
  // to 0 (`core/identity/auth_routes.py`), so 0 means "no TTL reported" and
  // never "expiring now". Nothing on this row counts it down.
  it("never renders expires_in as time remaining", () => {
    render(
      <SessionsSection
        sessions={sessions({ list: [authSession({ expires_in: 0, current: false })] })}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.queryByText(/expir/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/0:00/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Connected services
// ---------------------------------------------------------------------------

describe("ServicesSection", () => {
  it("names every service and what kind of thing it is", () => {
    render(<ServicesSection integrations={integrations()} />);
    expect(screen.getByRole("heading", { name: "Connected services" })).toBeInTheDocument();
    expect(screen.getByText("home-service")).toBeInTheDocument();
    // Not decorative: it is the only thing separating `weather` the adapter from
    // `weather` the registry service, and `--muted` is 3.20:1 on this card.
    const kind = screen.getByText("service · service");
    expect(hasClass(kind, "t-meta-strong")).toBe(true);
    expect(hasClass(kind, "t-meta")).toBe(false);
    expect(screen.getByText("weather · adapter")).toBeInTheDocument();
  });

  it("prints the state word in the token that reads on a card", () => {
    render(<ServicesSection integrations={integrations()} />);
    expect(screen.getByText("ok")).toHaveStyle({ color: "var(--green-text)" });
    // `--fg2`, not the handoff's `--muted`: 3.20:1 on `--surface` in light is
    // under AA, and this word is the whole state of the row.
    expect(screen.getByText("unset")).toHaveStyle({ color: "var(--fg2)" });
  });

  it("colours a failed service for attention and never for alarm", () => {
    render(
      <ServicesSection
        integrations={integrations({
          rows: { "home-service": row({ state: "failed", status: 502 }), weather: row() },
        })}
      />,
    );
    expect(screen.getByText("failed")).toHaveStyle({ color: "var(--accent-text)" });
  });

  // Fill versus outline, not hue: `--green` against `--muted` is 1.22:1 dark
  // and 1.51:1 light, two circles of near-identical luminance.
  it("separates a working service from a broken one without leaning on hue", () => {
    render(
      <ServicesSection
        integrations={integrations({
          rows: { "home-service": row(), weather: row({ state: "failed", status: 401 }) },
        })}
      />,
    );
    const [ok, failed] = screen.getAllByTestId("service-dot");
    expect(ok.style.background).toBe("var(--green-text)");
    expect(failed.style.background).toBe("transparent");
    expect(failed.style.borderColor).toBe("var(--accent-text)");
    // Redundant for the eye only: the word beside it says the same thing.
    expect(ok).toHaveAttribute("aria-hidden", "true");
  });

  // The handoff puts latency in the Health grid and nowhere else, so `ServiceRow`
  // no longer carries one. This holds the rendered section to it as well.
  it("leaves the round trip to the Health grid", () => {
    render(<ServicesSection integrations={integrations()} />);
    expect(screen.queryAllByText(/ ms$/)).toHaveLength(0);
  });

  // The section prints `serviceNote` and never a word of its own.
  it("says what is stored and what happens next, verbatim, for each state", () => {
    const states: [ServiceRow, string][] = [
      [row({ state: "ok" }), "stored encrypted at rest · last check ok"],
      [row({ state: "unset" }), "nothing stored · Alfred answers without this source"],
      [row({ state: "testing" }), "round-trip in progress · up to 20 s"],
      [row({ state: "queued" }), "saved · testing"],
      [
        row({ state: "failed", status: 502 }),
        "502 from the service on the last check · stored value kept until you replace it",
      ],
      [
        // No status on the wire and nothing said: the row must not invent one.
        row({ state: "failed", status: null }),
        "the last check came back unhealthy · stored value kept until you replace it",
      ],
      [
        // `_service_status` answers 200 with `healthy: false` and the reason in
        // `detail` for an unreachable service, which is the commonest failure.
        row({ state: "failed", status: null, detail: "connection refused" }),
        "connection refused · stored value kept until you replace it",
      ],
      [
        row({ state: "failed", status: 404 }),
        "404 · Alfred does not know this name · the service may have unregistered since the list was read",
      ],
    ];
    for (const [state, note] of states) {
      const { unmount } = render(
        <ServicesSection integrations={integrations({ list: [HOME], rows: { "home-service": state } })} />,
      );
      expect(screen.getByText(note)).toBeInTheDocument();
      expect(screen.getByText(note).textContent).toBe(
        serviceNote(state.state, state.status, state.detail),
      );
      unmount();
    }
  });

  // A 404 there is Alfred's own route answering, not the service: the registry
  // no longer carries the name.
  it("does not blame a service for Alfred's own 404", () => {
    render(
      <ServicesSection
        integrations={integrations({
          list: [HOME],
          rows: { "home-service": row({ state: "failed", status: 404 }) },
        })}
      />,
    );
    expect(screen.queryByText(/404 from the service/)).not.toBeInTheDocument();
  });

  it("says so when the registry carries nothing", () => {
    render(<ServicesSection integrations={integrations({ list: [], rows: {} })} />);
    expect(screen.getByText("No connected services.")).toBeInTheDocument();
  });

  // `IntegrationRegistry.available()` always yields the registered adapters, so
  // this sentence can only be true before the registry has answered — which is
  // exactly when it must not be shown.
  it("says nothing at all until a read has landed", () => {
    render(<ServicesSection integrations={integrations({ list: [], rows: {}, read: false })} />);
    expect(screen.queryByText("No connected services.")).not.toBeInTheDocument();
  });

  it("does not call a registry it could not read an empty one", () => {
    render(
      <ServicesSection
        integrations={integrations({ list: [], rows: {}, error: "Registry unavailable" })}
      />,
    );
    expect(screen.getByText("Registry unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No connected services.")).not.toBeInTheDocument();
  });

  // Every adapter in one category would collapse to a single row.
  it("keys a row on the service's name and not on its category", () => {
    const warn = vi.spyOn(console, "error").mockImplementation(() => {});
    const sibling = integration({ name: "calendar-service" });
    render(
      <ServicesSection
        integrations={integrations({
          list: [HOME, sibling],
          rows: { "home-service": row(), "calendar-service": row({ state: "unset" }) },
        })}
      />,
    );
    expect(screen.getByText("home-service")).toBeInTheDocument();
    expect(screen.getByText("calendar-service")).toBeInTheDocument();
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("carries its own read failure", () => {
    render(<ServicesSection integrations={integrations({ error: "Registry unavailable" })} />);
    expect(screen.getByText("Registry unavailable")).toBeInTheDocument();
    expect(screen.getByText("home-service")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Devices & identity
// ---------------------------------------------------------------------------

describe("IdentitySection", () => {
  it("names every passkey and the whole line the formatter builds", () => {
    render(
      <IdentitySection credentials={credentials()} pairing={pairing()} now={SYSTEM_NOW} />,
    );
    expect(screen.getByRole("heading", { name: "Devices & identity" })).toBeInTheDocument();
    expect(screen.getByText("Phone")).toBeInTheDocument();
    expect(
      screen.getByText("passkey · registered 12 Aug · internal · last used 07:02 · this device"),
    ).toBeInTheDocument();
    const meta = screen.getByText(credentialMeta(credential({ current: true }), SYSTEM_NOW));
    // The whole basis for keeping or removing a passkey, and `--muted` is
    // 3.20:1 on this card in light.
    expect(hasClass(meta, "t-meta-strong")).toBe(true);
    expect(hasClass(meta, "t-meta")).toBe(false);
    expect(hasClass(screen.getByText("Phone"), "t-row")).toBe(true);
  });

  it("counts what is registered beside the control that adds one", () => {
    render(
      <IdentitySection
        credentials={credentials({ list: [credential({ current: true }), credential({ credential_id: "cred-laptop", device_name: "Laptop" })] })}
        pairing={pairing()}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.getByText("2 registered")).toBeInTheDocument();
  });

  it("mints a code from the control that says it will", () => {
    const state = pairing();
    render(
      <IdentitySection credentials={credentials()} pairing={state} now={SYSTEM_NOW} />,
    );
    const add = screen.getByRole("button", { name: "Add a passkey on another device" });
    expect(add).toHaveStyle({ color: "var(--accent-text)" });
    expect(tall(add)).toBe(true);
    fireEvent.click(add);
    expect(state.mint).toHaveBeenCalledTimes(1);
  });

  it("shows nothing that looks like a code until one has been minted", () => {
    render(<IdentitySection credentials={credentials()} pairing={pairing()} now={SYSTEM_NOW} />);
    expect(screen.queryByTestId("pairing-code")).not.toBeInTheDocument();
    expect(screen.queryByText(/Pairing window closes/)).not.toBeInTheDocument();
  });

  it("shows the code big enough to read off a screen, and says it will not come back", () => {
    render(
      <IdentitySection
        credentials={credentials()}
        pairing={pairing({ code: "042317", expiresAt: PAIRING_CLOSES })}
        now={SYSTEM_NOW}
      />,
    );
    const code = screen.getByTestId("pairing-code");
    expect(code).toHaveTextContent("042317");
    expect(code).toHaveStyle({ fontSize: "32px", letterSpacing: "0.18em" });
    expect(hasClass(code, "font-mono")).toBe(true);
    // Read off this screen and typed into another one, so it carries `--fg`
    // rather than the card's meta token.
    expect(code.style.color).toBe("var(--fg)");
    // There is no way to re-show it, and the note is where that is said.
    const note = screen.getByText(
      "Pairing window closes 21:20 · enter this on the new device · not shown again once you leave",
    );
    // Wired to the control that minted it: unannounced, the one clause the
    // reader must act on before leaving is the one they never hear.
    expect(
      screen.getByRole("button", { name: "Add a passkey on another device" }),
    ).toHaveAttribute("aria-describedby", note.id);
  });

  it("describes nothing while there is no code to describe", () => {
    render(<IdentitySection credentials={credentials()} pairing={pairing()} now={SYSTEM_NOW} />);
    expect(
      screen.getByRole("button", { name: "Add a passkey on another device" }),
    ).not.toHaveAttribute("aria-describedby");
  });

  // An invented window is worse than none: the code would carry a closing time
  // the server never stated.
  it("drops the closing time rather than faking one", () => {
    render(
      <IdentitySection
        credentials={credentials()}
        pairing={pairing({ code: "042317", expiresAt: "whenever" })}
        now={SYSTEM_NOW}
      />,
    );
    expect(
      screen.getByText("enter this on the new device · not shown again once you leave"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Pairing window closes/)).not.toBeInTheDocument();
  });

  it("refuses a second mint while the first is still going", () => {
    const state = pairing({ minting: true });
    render(<IdentitySection credentials={credentials()} pairing={state} now={SYSTEM_NOW} />);
    const add = screen.getByRole("button", { name: "Add a passkey on another device" });
    expect(add).toHaveAttribute("aria-disabled", "true");
    expect(add).toHaveAttribute("aria-busy", "true");
    fireEvent.click(add);
    expect(state.mint).not.toHaveBeenCalled();
  });

  it("signs this device out from the control that says so", () => {
    const state = credentials();
    render(<IdentitySection credentials={state} pairing={pairing()} now={SYSTEM_NOW} />);
    const out = screen.getByRole("button", { name: "Sign out on this device" });
    expect(tall(out)).toBe(true);
    // Plain, not accent: the card's one "look here" colour belongs to the
    // control that adds a device, not the one that takes the house away.
    expect(out.style.color).toBe("var(--fg)");
    expect(out.style.fontWeight).toBe("400");
    fireEvent.click(out);
    expect(state.signOut).toHaveBeenCalledTimes(1);
  });

  it("refuses a second sign-out while the first is in flight", () => {
    const state = credentials({ signingOut: true });
    render(<IdentitySection credentials={state} pairing={pairing()} now={SYSTEM_NOW} />);
    const out = screen.getByRole("button", { name: "Sign out on this device" });
    expect(out).toHaveAttribute("aria-disabled", "true");
    expect(out).toHaveAttribute("aria-busy", "true");
    expect(out).not.toBeDisabled();
    fireEvent.click(out);
    expect(state.signOut).not.toHaveBeenCalled();
  });

  // No delete: `DELETE /api/auth/credentials/{id}` exists and refuses the last
  // one with a 409, but removing the passkey in your hand is a foot-gun with no
  // confirmation design. Task 11 backlogs it.
  it("offers no way to remove a passkey", () => {
    render(<IdentitySection credentials={credentials()} pairing={pairing()} now={SYSTEM_NOW} />);
    for (const name of [/remove/i, /delete/i, /forget/i]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("carries the refusals its two writes can answer with", () => {
    render(
      <IdentitySection
        credentials={credentials({ error: "Session store unavailable" })}
        pairing={pairing({ error: "Redis unavailable" })}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.getByText("Session store unavailable")).toBeInTheDocument();
    const mintFailed = screen.getByText("Redis unavailable");
    // Under the control it belongs to and above the sign-out control, not at
    // the end of the card where it would read as the sign-out's refusal.
    const add = screen.getByRole("button", { name: "Add a passkey on another device" });
    const out = screen.getByRole("button", { name: "Sign out on this device" });
    expect(add.compareDocumentPosition(mintFailed) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(mintFailed.compareDocumentPosition(out) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // And it is that button's own description: a reader who presses `Add a
    // passkey` and is answered by a note somewhere else in the card is
    // answered by nothing. The code note is wired the same way.
    expect(add).toHaveAttribute("aria-describedby", mintFailed.id);
    expect(mintFailed.id).not.toBe("");
  });

  it("says so when a read has landed and found no passkey", () => {
    render(
      <IdentitySection credentials={credentials({ list: [] })} pairing={pairing()} now={SYSTEM_NOW} />,
    );
    expect(screen.getByText("No passkeys registered.")).toBeInTheDocument();
    expect(screen.getByText("0 registered")).toBeInTheDocument();
  });

  // A reader holding a passkey would be told there are none — and `0 registered`
  // is a count of a list nobody has read, which is a claim rather than a blank.
  it("neither denies nor counts passkeys before a read has landed", () => {
    render(
      <IdentitySection
        credentials={credentials({ list: [], read: false })}
        pairing={pairing()}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.queryByText("No passkeys registered.")).not.toBeInTheDocument();
    expect(screen.queryByText("0 registered")).not.toBeInTheDocument();
  });

  it("does not call a list it could not read an empty one", () => {
    render(
      <IdentitySection
        credentials={credentials({ list: [], error: "Session store unavailable" })}
        pairing={pairing()}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.getByText("Session store unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No passkeys registered.")).not.toBeInTheDocument();
  });

  it("keys a passkey on its credential id and not on the device's name", () => {
    const warn = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <IdentitySection
        credentials={credentials({
          list: [
            credential({ credential_id: "cred-a", device_name: "Laptop", current: true }),
            credential({ credential_id: "cred-b", device_name: "Laptop" }),
          ],
        })}
        pairing={pairing()}
        now={SYSTEM_NOW}
      />,
    );
    expect(screen.getAllByText("Laptop")).toHaveLength(2);
    expect(screen.getByText("2 registered")).toBeInTheDocument();
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  // Literal ids would make two cards on one document describe each other.
  it("gives each card its own ids", () => {
    const state = pairing({ code: "042317", expiresAt: PAIRING_CLOSES });
    render(
      <>
        <IdentitySection credentials={credentials()} pairing={state} now={SYSTEM_NOW} />
        <IdentitySection credentials={credentials()} pairing={state} now={SYSTEM_NOW} />
      </>,
    );
    const [first, second] = screen.getAllByRole("button", {
      name: "Add a passkey on another device",
    });
    expect(first.getAttribute("aria-describedby")).not.toBe(
      second.getAttribute("aria-describedby"),
    );
  });
});

// ---------------------------------------------------------------------------
// Reflex
// ---------------------------------------------------------------------------

describe("ReflexSection", () => {
  it("opens with the sentence that says what the set means", () => {
    render(<ReflexSection attention={attention()} />);
    expect(screen.getByRole("heading", { name: "Reflex" })).toBeInTheDocument();
    expect(screen.getByText(ATTENTION_INTRO)).toBeInTheDocument();
    expect(hasClass(screen.getByText(ATTENTION_INTRO), "t-meta-strong")).toBe(true);
  });

  it("gives every domain its own sub-heading", () => {
    render(<ReflexSection attention={attention()} />);
    expect(screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent)).toEqual([
      "light",
      "lock",
    ]);
  });

  it("takes a member back to asking first", () => {
    const state = attention();
    render(<ReflexSection attention={state} />);
    const chip = screen.getByRole("button", { name: "light.hall · ask before acting" });
    expect(chip).toHaveTextContent("light.hall");
    // The symbol carries no meaning a reader could not get from the name, and
    // announced it would say the entity twice.
    const symbol = within(chip).getByText("×");
    expect(symbol).toHaveAttribute("aria-hidden", "true");
    expect(chip).toHaveStyle({ background: "var(--ink)", color: "var(--paper)" });
    expect(tall(chip)).toBe(true);
    fireEvent.click(chip);
    expect(state.ask).toHaveBeenCalledWith("light", "light.hall");
    expect(state.allow).not.toHaveBeenCalled();
  });

  it("lets an observed entity be acted on without asking", () => {
    const state = attention();
    render(<ReflexSection attention={state} />);
    const chip = screen.getByRole("button", { name: "light.study · act without asking" });
    expect(within(chip).getByText("+")).toHaveAttribute("aria-hidden", "true");
    // Hollow: a bordered chip on the card rather than a filled one.
    expect(chip.style.background).toBe("transparent");
    expect(chip.style.borderColor).toBe("var(--muted)");
    expect(chip.style.color).toBe("var(--fg2)");
    fireEvent.click(chip);
    expect(state.allow).toHaveBeenCalledWith("light", "light.study");
    expect(state.ask).not.toHaveBeenCalled();
  });

  // `seen` is everything observed, members included; a member drawn twice would
  // offer the same entity both ways at once.
  it("draws an entity once, on the side it is actually on", () => {
    render(<ReflexSection attention={attention()} />);
    expect(screen.getAllByRole("button", { name: /^light\.hall/ })).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "light.hall · act without asking" }),
    ).not.toBeInTheDocument();
  });

  it("holds a domain's chips while its write is in flight", () => {
    const state = attention({ saving: { light: true } });
    render(<ReflexSection attention={state} />);
    const held = screen.getByRole("button", { name: "light.hall · ask before acting" });
    expect(held).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(held);
    expect(state.ask).not.toHaveBeenCalled();
    // A write on one domain says nothing about another.
    fireEvent.click(screen.getByRole("button", { name: "lock.front · act without asking" }));
    expect(state.allow).toHaveBeenCalledWith("lock", "lock.front");
  });

  it("says so when the set is empty", () => {
    render(<ReflexSection attention={attention({ domains: [] })} />);
    expect(screen.getByText("Nothing is on the attention set yet.")).toBeInTheDocument();
  });

  it("says nothing at all until a read has landed", () => {
    render(<ReflexSection attention={attention({ domains: [], read: false })} />);
    expect(screen.queryByText("Nothing is on the attention set yet.")).not.toBeInTheDocument();
    // The sentence that says what the set means is not a claim about its
    // contents, so it stands whatever has answered.
    expect(screen.getByText(ATTENTION_INTRO)).toBeInTheDocument();
  });

  // `attentionSaving` is keyed by domain and so is this: pressing a chip in one
  // domain must not erase the refusal another is still showing.
  it("puts a refused write under the domain that was refused", () => {
    render(
      <ReflexSection attention={attention({ failed: { light: "503 · Attention store unavailable" } })} />,
    );
    const refusal = screen.getByText("503 · Attention store unavailable");
    expect(hasClass(refusal, "t-meta-strong")).toBe(true);
    // Inside `light`'s block, below its chips — not at the top of the card.
    const heading = screen.getByRole("heading", { level: 4, name: "light" });
    const block = heading.parentElement as HTMLElement;
    expect(block.contains(refusal)).toBe(true);
    expect(within(block).getByRole("button", { name: /^light\.hall/ })).toBeInTheDocument();
    const chip = screen.getByRole("button", { name: "light.hall · ask before acting" });
    expect(chip.compareDocumentPosition(refusal) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  // The endpoint distinguishes "nothing configured" from "the store is down",
  // and this section must not flatten the two.
  it("reports a store that is down rather than calling the set empty", () => {
    render(
      <ReflexSection attention={attention({ domains: [], error: "Attention store unavailable" })} />,
    );
    expect(screen.getByText("Attention store unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Nothing is on the attention set yet.")).not.toBeInTheDocument();
  });

  it("keeps the chips it was last told about behind a failed write", () => {
    render(<ReflexSection attention={attention({ error: "Attention store unavailable" })} />);
    expect(screen.getByText("Attention store unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "light.hall · ask before acting" })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// What all four owe the reader
// ---------------------------------------------------------------------------

describe("SystemSections", () => {
  /**
   * All four at once, with a service row opened so the bench's only `<form>` is
   * really on the page. Without it the rule below has nothing to bite on — a
   * default-typed button is only dangerous inside a form, and Save is the one
   * control that stands in one.
   */
  function drawAll() {
    render(
      <>
        <SessionsSection sessions={sessions()} now={SYSTEM_NOW} />
        <ServicesSection integrations={integrations()} />
        <IdentitySection
          credentials={credentials()}
          pairing={pairing({ code: "042317", expiresAt: PAIRING_CLOSES })}
          now={SYSTEM_NOW}
        />
        <ReflexSection attention={attention()} />
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: /home-service/ }));
  }

  // Every one of these is the reason the rows under it may be out of date, so
  // it is the last line on the bench that may be hard to read.
  it("writes every section's failure in the type that carries it", () => {
    render(
      <>
        <SessionsSection sessions={sessions({ error: "Session store unavailable" })} now={SYSTEM_NOW} />
        <ServicesSection integrations={integrations({ error: "Registry unavailable" })} />
        <IdentitySection
          credentials={credentials({ error: "Passkey store unavailable" })}
          pairing={pairing({ error: "Redis unavailable" })}
          now={SYSTEM_NOW}
        />
        <ReflexSection attention={attention({ error: "Attention store unavailable" })} />
      </>,
    );
    for (const text of [
      "Session store unavailable",
      "Registry unavailable",
      "Passkey store unavailable",
      "Redis unavailable",
      "Attention store unavailable",
    ]) {
      const note = screen.getByText(text);
      expect(hasClass(note, "t-meta-strong")).toBe(true);
      expect(hasClass(note, "t-meta")).toBe(false);
      expect(note.style.color).toBe("");
    }
  });

  it("keeps every control at a thumb's height", () => {
    drawAll();
    const controls = screen.getAllByRole("button");
    expect(controls.length).toBeGreaterThan(8);
    for (const control of controls) {
      expect(tall(control)).toBe(true);
    }
  });

  // A button with no explicit type submits the form it is standing in. Exactly
  // one control on these four sections is allowed to do that, and it has to be
  // inside the form it submits.
  it("lets nothing submit a form by accident", () => {
    drawAll();
    expect(document.querySelectorAll("form")).toHaveLength(1);
    const submits = screen
      .getAllByRole("button")
      .filter((control) => control.getAttribute("type") === "submit");
    expect(submits).toHaveLength(1);
    expect(submits[0]).toHaveAccessibleName("Save & test");
    expect(submits[0].closest("form")).not.toBeNull();

    for (const control of screen.getAllByRole("button")) {
      if (control.getAttribute("type") === "submit") continue;
      expect(control).toHaveAttribute("type", "button");
      expect(control.closest("form")).toBeNull();
    }
  });
});
