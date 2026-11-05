import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PushValue, Reach } from "@/shell/usePush";
import { authSession } from "@/test/fixtures";
import { ReachSection } from "./ReachSection";
import type { Sessions } from "./useSystem";

/** What `usePush()` returns, set per test; `rerender` picks up a change. */
const push = vi.hoisted(() => ({ value: null as unknown as PushValue }));
vi.mock("@/shell/usePush", () => ({ usePush: () => push.value }));

function setPush(reach: Reach, asking = false) {
  push.value = { ...push.value, reach, asking };
}

// Alfred.dc.html:389-395 and the brief's table, verbatim; D1–D4, D7 and D8 from
// the plans' Deviations tables.
const INSTALL_SUB = "unavailable in Safari · add to home screen first";
const ASK_SUB = "off · not yet asked · iOS asks once";
const ON_SUB = "on · this iPhone · important and urgent only";
const ON_BADGE_SUB =
  "on · this iPhone · important and urgent only · badge = notifications since you last opened";
const DENIED_SUB = "off · declined in iOS · this app cannot flip it";
const DENIED_NOTE =
  "Settings › Alfred › Notifications › Allow. iOS holds this switch now; nothing in here can flip it, and Alfred will not raise it again.";
const UNAVAILABLE_SUB = "unavailable here · this browser cannot receive push";
const UNCONFIGURED_SUB = "unavailable · Web Push is not set up on the house";
const CHECKING_SUB = "checking · allowed in iOS · registering with the house";
const FAILED_SUB = "off · allowed in iOS · the house did not take the registration";

const BUSY = "Device store busy";

/** A local wall-clock time, so `hhmm` reads 07:02 in any time zone. */
const SIGNED_IN_AT = new Date(2026, 9, 3, 7, 2, 41).toISOString();

function sessions(overrides: Partial<Sessions> = {}): Sessions {
  return {
    list: [
      authSession({
        session_id: "sess-laptop",
        created_at: new Date(2026, 9, 2, 9, 15).toISOString(),
      }),
      authSession({ current: true, created_at: SIGNED_IN_AT }),
    ],
    read: true,
    ended: {},
    ending: {},
    failed: {},
    end: vi.fn(),
    error: null,
    ...overrides,
  };
}

const UNREAD: Partial<Sessions> = { list: [], read: false };

function section(overrides: Partial<Sessions> = {}) {
  return <ReachSection sessions={sessions(overrides)} />;
}

/** The row's own words: its label and the sub beneath it. */
const sub = () => screen.getByText("Push notifications").nextElementSibling;
const dot = () => screen.queryByTestId("service-dot");
const standalone = () => screen.queryByRole("dialog", { name: "You came in the front way" });
const install = () => screen.queryByRole("dialog", { name: "Add me to the home screen" });

function step(label: string): HTMLElement {
  const li = screen.getByText(label).closest("li");
  if (!li) throw new Error(`no step row for ${label}`);
  return li;
}

beforeEach(() => {
  push.value = {
    reach: { kind: "ask" },
    asking: false,
    enable: vi.fn(),
    retry: vi.fn(),
  };
  vi.stubGlobal("location", { hostname: "alfred.example.com", pathname: "/" });
});

afterEach(() => {
  Reflect.deleteProperty(navigator, "setAppBadge");
});

/** A browser with an icon to put a number on. jsdom's has none. */
function badging(): void {
  Object.defineProperty(navigator, "setAppBadge", { configurable: true, value: vi.fn() });
}

type Right = { action: string } | { word: string; colour: string; fill: string; ring: string };

const GREEN = "var(--green-text)";
const ACCENT = "var(--accent-text)";

describe("ReachSection", () => {
  it.each<[Reach["kind"], Reach, string, Right]>([
    ["install", { kind: "install" }, INSTALL_SUB, { action: "Show me how" }],
    ["ask", { kind: "ask" }, ASK_SUB, { action: "Enable" }],
    ["on", { kind: "on" }, ON_SUB, { word: "on", colour: GREEN, fill: GREEN, ring: GREEN }],
    [
      "denied",
      { kind: "denied" },
      DENIED_SUB,
      { word: "off", colour: ACCENT, fill: "transparent", ring: ACCENT },
    ],
    [
      "unavailable",
      { kind: "unavailable" },
      UNAVAILABLE_SUB,
      {
        word: "off",
        colour: "var(--fg2)",
        fill: "transparent",
        ring: "var(--muted)",
      },
    ],
    [
      "unconfigured",
      { kind: "unconfigured" },
      UNCONFIGURED_SUB,
      {
        word: "off",
        colour: "var(--fg2)",
        fill: "transparent",
        ring: "var(--muted)",
      },
    ],
    [
      "checking",
      { kind: "checking" },
      CHECKING_SUB,
      {
        word: "—",
        colour: "var(--fg2)",
        fill: "transparent",
        ring: "var(--muted)",
      },
    ],
    ["failed", { kind: "failed", detail: BUSY }, FAILED_SUB, { action: "Try again" }],
  ])("says %s in the handoff's words", (_kind, reach, words, right) => {
    setPush(reach);
    render(section());

    expect(screen.getByRole("heading", { level: 3, name: "Reach" })).toBeInTheDocument();
    expect(sub()?.textContent).toBe(words);

    if ("action" in right) {
      // An action, and no state beside it: the handoff draws one or the other.
      const button = screen.getByRole("button", { name: right.action });
      expect(button).toHaveAttribute("type", "button");
      // 44 px, accent 500 13 px, pulled 8 px into the row's padding (`:390`).
      expect(button).toHaveClass("h-11", "-mr-2", "text-[13px]", "font-medium");
      // The inline value, not the computed one: jsdom resolves no `var()`.
      expect(button.style.color).toBe(ACCENT);
      expect(dot()).toBeNull();
    } else {
      expect(screen.queryByRole("button")).toBeNull();
      const word = screen.getByText(right.word);
      expect(word.textContent).toBe(right.word);
      // Connected services' own word colours and dot, not a second set.
      expect(word.style.color).toBe(right.colour);
      const { background, borderColor } = (dot() as HTMLElement).style;
      expect({ background, borderColor }).toEqual({
        background: right.fill,
        borderColor: right.ring,
      });
    }
  });

  it("names the badge in the on line where setAppBadge exists", () => {
    badging();
    setPush({ kind: "on" });
    render(section());
    expect(sub()?.textContent).toBe(ON_BADGE_SUB);
  });

  it("leaves the clause out elsewhere", () => {
    setPush({ kind: "on" });
    render(section());
    expect(sub()?.textContent).toBe(ON_SUB);
  });

  it("keeps the clause to the on line", () => {
    badging();
    setPush({ kind: "denied" });
    render(section());
    expect(sub()?.textContent).toBe(DENIED_SUB);
  });

  it("Show me how opens the install gate, and Not now closes it", async () => {
    const user = userEvent.setup();
    setPush({ kind: "install" });
    render(section());
    expect(install()).toBeNull();

    await user.click(screen.getByRole("button", { name: "Show me how" }));

    const gate = install();
    expect(gate).not.toBeNull();
    expect(
      within(gate as HTMLElement).getByRole("heading", {
        name: "Add me to the home screen.",
      }),
    ).toBeInTheDocument();

    await user.click(within(gate as HTMLElement).getByRole("button", { name: "Not now" }));

    await waitFor(() => expect(install()).toBeNull());
    expect(push.value.enable).not.toHaveBeenCalled();
  });

  it("Enable opens the standalone gate at step five", async () => {
    const user = userEvent.setup();
    setPush({ kind: "ask" });
    render(section());
    expect(standalone()).toBeNull();

    await user.click(screen.getByRole("button", { name: "Enable" }));

    expect(standalone()).not.toBeNull();
    expect(
      [
        "Open in Safari",
        "Sign in with Face ID",
        "Add to Home Screen",
        "Open from the home screen",
        "Let Alfred reach you",
        "Allow in iOS",
      ].map((label) => step(label).getAttribute("data-step-state")),
    ).toEqual(["done", "done", "done", "done", "current", "todo"]);
    // D5: this host, and when *this* session began — not the other one's.
    expect(within(step("Open in Safari")).getByText("alfred.example.com")).toBeInTheDocument();
    expect(within(step("Sign in with Face ID")).getByText("07:02")).toBeInTheDocument();
    // Opening the gate asks nothing: only its own primary does.
    expect(push.value.enable).not.toHaveBeenCalled();

    // Not yet only closes it.
    await user.click(screen.getByRole("button", { name: "Not yet" }));
    await waitFor(() => expect(standalone()).toBeNull());
    expect(push.value.enable).not.toHaveBeenCalled();
  });

  it("leaves the sign-in time off the gate until the sessions read lands", async () => {
    const user = userEvent.setup();
    setPush({ kind: "ask" });
    render(section(UNREAD));

    await user.click(screen.getByRole("button", { name: "Enable" }));

    expect(step("Sign in with Face ID")).not.toHaveTextContent(/\d/);
  });

  it("Let me reach you enables, holds while asking, and closes when done", async () => {
    const user = userEvent.setup();
    setPush({ kind: "ask" });
    const { rerender } = render(section());
    await user.click(screen.getByRole("button", { name: "Enable" }));

    const allow = screen.getByRole("button", { name: "Let me reach you" });
    await user.click(allow);
    expect(push.value.enable).toHaveBeenCalledTimes(1);

    // The prompt is up: the primary holds, and a second press asks nothing.
    setPush({ kind: "ask" }, true);
    rerender(section());
    expect(allow).toHaveAttribute("aria-busy", "true");
    expect(allow).toHaveAttribute("aria-disabled", "true");
    await user.click(allow);
    expect(push.value.enable).toHaveBeenCalledTimes(1);
    expect(standalone()).not.toBeNull();

    // Answered: the gate goes, and the row says what came of it.
    setPush({ kind: "on" }, false);
    rerender(section());
    await waitFor(() => expect(standalone()).toBeNull());
    expect(sub()?.textContent).toBe(ON_SUB);
  });

  it("Try again retries", async () => {
    const user = userEvent.setup();
    setPush({ kind: "failed", detail: BUSY });
    render(section());

    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(push.value.retry).toHaveBeenCalledTimes(1);
    expect(push.value.enable).not.toHaveBeenCalled();
    // Retrying is not asking: no gate.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the denied note only when denied", () => {
    const kinds: Reach[] = [
      { kind: "install" },
      { kind: "ask" },
      { kind: "on" },
      { kind: "denied" },
      { kind: "unavailable" },
      { kind: "unconfigured" },
      { kind: "checking" },
      { kind: "failed", detail: BUSY },
    ];
    const shown = kinds.map((reach) => {
      setPush(reach);
      const { unmount } = render(section());
      const note = screen.queryByText(DENIED_NOTE);
      if (note !== null) expect(note.textContent).toBe(DENIED_NOTE);
      unmount();
      return note !== null;
    });
    expect(shown).toEqual(kinds.map((reach) => reach.kind === "denied"));
  });

  it("shows the failure in the note row", () => {
    // As the house or the browser said it, a timeout's own words included: new
    // copy here would be a deviation the plan does not have.
    const detail = "The operation timed out.";
    setPush({ kind: "failed", detail });
    render(section());

    const note = screen.getByText(detail);
    expect(note.textContent).toBe(detail);
    expect(screen.queryByText(DENIED_NOTE)).toBeNull();
    // The action is described by the row's sub and by the failure under it.
    const button = screen.getByRole("button", { name: "Try again" });
    expect(button).toHaveAccessibleDescription(`${FAILED_SUB} ${detail}`);
  });
});
