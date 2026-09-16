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
  ip: "192.168.1.31",
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
  return { list: [PHONE, LAPTOP], ended: {}, ending: {}, end: vi.fn(), error: null, ...overrides };
}

const row = (overrides: Partial<ServiceRow> = {}): ServiceRow => ({
  state: "ok",
  latency: 210,
  status: null,
  ...overrides,
});

function integrations(overrides: Partial<Integrations> = {}): Integrations {
  return {
    list: [HOME, WEATHER],
    rows: { "home-service": row(), weather: row({ state: "unset", latency: null }) },
    saves: {},
    save: vi.fn(),
    error: null,
    ...overrides,
  };
}

function credentials(overrides: Partial<Credentials> = {}): Credentials {
  return {
    list: [credential({ current: true })],
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
    saving: {},
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
    // Decorative meta: the name above it and the note below carry the facts.
    const kind = screen.getByText("service · service");
    expect(hasClass(kind, "t-meta")).toBe(true);
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
          rows: { "home-service": row({ state: "failed", latency: null, status: 502 }), weather: row() },
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
          rows: { "home-service": row(), weather: row({ state: "failed", latency: null, status: 401 }) },
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

  it("times the round trip it has one for, and stays silent about the ones it does not", () => {
    render(<ServicesSection integrations={integrations()} />);
    expect(screen.getByText("210 ms")).toBeInTheDocument();
    // A failed attempt has no round trip of its own, and the retained number
    // beside it belongs to an earlier one.
    expect(screen.queryAllByText(/ ms$/)).toHaveLength(1);
  });

  // The section prints `serviceNote` and never a word of its own.
  it("says what is stored and what happens next, verbatim, for each state", () => {
    const states: [ServiceRow, string][] = [
      [row({ state: "ok" }), "stored encrypted at rest · last check ok"],
      [row({ state: "unset" }), "nothing stored · Alfred answers without this source"],
      [row({ state: "testing" }), "round-trip in progress · up to 10 s"],
      [row({ state: "queued" }), "saved · testing"],
      [
        row({ state: "failed", status: 502 }),
        "502 from the service on the last check · stored value kept until you replace it",
      ],
      [
        row({ state: "failed", status: null }),
        "401 from the service on the last check · stored value kept until you replace it",
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
      expect(screen.getByText(note).textContent).toBe(serviceNote(state.state, state.status));
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
          rows: { "home-service": row({ state: "failed", latency: null, status: 404 }) },
        })}
      />,
    );
    expect(screen.queryByText(/404 from the service/)).not.toBeInTheDocument();
  });

  it("says so when the registry carries nothing", () => {
    render(<ServicesSection integrations={integrations({ list: [], rows: {} })} />);
    expect(screen.getByText("No connected services.")).toBeInTheDocument();
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
    expect(
      screen.getByText(credentialMeta(credential({ current: true }), SYSTEM_NOW)),
    ).toBeInTheDocument();
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
    // There is no way to re-show it, and the note is where that is said.
    expect(
      screen.getByText(
        "Pairing window closes 21:20 · enter this on the new device · not shown again once you leave",
      ),
    ).toBeInTheDocument();
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
    expect(screen.getByText("Redis unavailable")).toBeInTheDocument();
  });

  it("says so when no passkey has been read", () => {
    render(
      <IdentitySection credentials={credentials({ list: [] })} pairing={pairing()} now={SYSTEM_NOW} />,
    );
    expect(screen.getByText("No passkeys registered.")).toBeInTheDocument();
    expect(screen.getByText("0 registered")).toBeInTheDocument();
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
  it("keeps every control at a thumb's height and out of any form it stands in", () => {
    render(
      <>
        <SessionsSection sessions={sessions()} now={SYSTEM_NOW} />
        <IdentitySection
          credentials={credentials()}
          pairing={pairing({ code: "042317", expiresAt: PAIRING_CLOSES })}
          now={SYSTEM_NOW}
        />
        <ReflexSection attention={attention()} />
      </>,
    );
    const controls = screen.getAllByRole("button");
    expect(controls.length).toBeGreaterThan(4);
    for (const control of controls) {
      expect(tall(control)).toBe(true);
      // A button with no explicit type submits the form it is standing in, and
      // the services section puts one around a credential form.
      expect(control).toHaveAttribute("type", "button");
    }
  });
});
