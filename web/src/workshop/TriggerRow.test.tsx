import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Trigger } from "@/lib/triggers";
import { TRIGGER_NOW, trigger } from "@/test/fixtures";
import { TriggerRow } from "./TriggerRow";
import type { Pending } from "./useTriggers";

/**
 * 21:15 the same evening the fixture trigger was written — when this client
 * sent the request every note below quotes. Local like its neighbours in
 * `fixtures.ts`: `hhmm` reads the device's clock, so a UTC instant would stamp
 * one string in CI and another on a developer's machine.
 */
const QUEUED_AT = new Date(2026, 8, 16, 21, 15, 0).getTime();
const FIRED_ISO = new Date(2026, 8, 16, 21, 15, 0).toISOString();

interface RowState {
  pending?: Pending;
  firedAt?: number;
  open?: boolean;
}

/** One row in the list it lives in, as `RoutineRow.test.tsx` renders its own. */
function renderRow(overrides: Partial<Trigger> = {}, props: RowState = {}) {
  const onToggleOpen = vi.fn();
  const onToggle = vi.fn();
  const onFire = vi.fn();
  const row = trigger(overrides);
  const draw = (state: RowState) => (
    <ul>
      <TriggerRow
        trigger={row}
        now={TRIGGER_NOW}
        pending={state.pending}
        firedAt={state.firedAt}
        open={state.open ?? false}
        onToggleOpen={onToggleOpen}
        onToggle={onToggle}
        onFire={onFire}
      />
    </ul>
  );
  const { rerender } = render(draw(props));
  return {
    row,
    onToggleOpen,
    onToggle,
    onFire,
    /** Re-render the same row with a later state, the way the hook would. */
    update: (next: RowState) => rerender(draw(next)),
  };
}

const rowButton = () => screen.getByRole("button", { expanded: false });
const openRowButton = () => screen.getByRole("button", { expanded: true });
const knob = () => screen.getByTestId("switch-knob");

const FIRE_NOTE = "queued only; look for trigger.fired on the events stream to know it ran";

describe("TriggerRow", () => {
  it("names the kind, the trigger and what it waits for", () => {
    renderRow();
    expect(screen.getByText("time")).toBeInTheDocument();
    expect(screen.getByText("Bins out")).toBeInTheDocument();
    expect(
      screen.getByText("recurring · runs 08:40 tomorrow · created from conversation 20:52"),
    ).toBeInTheDocument();
  });

  // The handoff's mono 10 accent kind; `--accent-text` rather than the raw
  // accent, which is 2.34:1 on paper (index.css, --accent-text).
  it("writes the kind in the accent that can be read as text", () => {
    renderRow({ trigger_type: "sensor", conditions: { entity_id: "binary_sensor.front_door" } });
    const kind = screen.getByText("sensor");
    expect(kind.style.color).toBe("var(--accent-text)");
    expect(kind).toHaveClass("font-mono");
  });

  it("reports the stored enabled state on the switch", () => {
    renderRow({ enabled: false });
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("switch")).not.toHaveAttribute("aria-busy");
  });

  // Every switch on the bench would otherwise be announced as an unnamed one.
  it("names the switch after the trigger it belongs to", () => {
    renderRow();
    expect(screen.getByRole("switch", { name: "Bins out" })).toBeInTheDocument();
  });

  it("hands a tap on the switch back with the trigger it belongs to", () => {
    const { onToggle, row } = renderRow();
    fireEvent.click(screen.getByRole("switch"));
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledWith(row);
  });

  // The assertion decision 6 exists for: the server queued a change and this
  // client does not know the trigger's state, so nothing about the row moves.
  it("leaves the switch exactly where it was while a change is queued", () => {
    const { onToggle } = renderRow(
      { enabled: false },
      { pending: { kind: "enabling", at: QUEUED_AT } },
    );
    const control = screen.getByRole("switch");
    expect(control).toHaveAttribute("aria-checked", "false");
    expect(control).toHaveAttribute("aria-busy", "true");
    // `aria-disabled`, not `disabled`: a control that disables itself under the
    // finger that pressed it throws focus to `<body>`, and a real `disabled`
    // would never announce the description below to the reader who asked for it.
    expect(control).toHaveAttribute("aria-disabled", "true");
    expect(control).not.toBeDisabled();
    expect(knob().style.left).toBe("3px");
    fireEvent.click(control);
    expect(onToggle).not.toHaveBeenCalled();
  });

  // WCAG 1.4.11: 3:1 for the boundary and 3:1 for whatever shows the state.
  // The ratios themselves are `test/contrast.test.ts`'s; this is the wiring.
  it("paints an off switch so that both its edge and its knob can be seen", () => {
    renderRow({ enabled: false });
    const off = screen.getByRole("switch");
    expect(off.style.background).toBe("var(--line)");
    expect(off.style.boxShadow).toBe("inset 0 0 0 1px var(--muted)");
    expect(knob().style.background).toBe("var(--fg2)");
  });

  it("paints an on switch the same way, in the tokens that read on accent", () => {
    renderRow({ enabled: true });
    const on = screen.getByRole("switch");
    expect(on.style.background).toBe("var(--accent)");
    expect(on.style.boxShadow).toBe("inset 0 0 0 1px var(--muted)");
    expect(knob().style.background).toBe("var(--on-accent)");
  });

  it("says when the change was queued and how long it may take", () => {
    renderRow({ enabled: false }, { pending: { kind: "enabling", at: QUEUED_AT } });
    const note = screen.getByText("queued 21:15 · enabling · takes effect within 60 s");
    expect(note.style.color).toBe("var(--accent-text)");
    expect(note).toHaveClass("t-meta-strong");
    // The note is the switch's own description, which is the only way a reader
    // who cannot see it learns what happened when they pressed it.
    expect(screen.getByRole("switch")).toHaveAttribute("aria-describedby", note.id);
    expect(note.id).not.toBe("");
  });

  it("says disabling for a change in the other direction", () => {
    renderRow({ enabled: true }, { pending: { kind: "disabling", at: QUEUED_AT } });
    expect(
      screen.getByText("queued 21:15 · disabling · takes effect within 60 s"),
    ).toBeInTheDocument();
    expect(knob().style.left).toBe("23px");
  });

  it("says a refused toggle did not land, and that the old setting stands", () => {
    renderRow(
      { enabled: true },
      { pending: { kind: "disabling", at: QUEUED_AT, error: "Trigger not found", status: 404 } },
    );
    const refusal = screen.getByText(
      "404 · that did not land · the scheduler still has the old setting",
    );
    expect(refusal).toBeInTheDocument();
    const control = screen.getByRole("switch");
    expect(control).toHaveAttribute("aria-checked", "true");
    // `aria-checked` alone discriminates nothing here — the switch never moves
    // in either branch. Waiting and refused are the two facts that differ: the
    // request has landed and been turned down, so there is nothing in flight.
    expect(control).not.toHaveAttribute("aria-busy");
    // Still inert, and for the other reason: a second press would queue a
    // request against a state neither end agrees on.
    expect(control).toHaveAttribute("aria-disabled", "true");
    // A refusal is settled, so it keeps `.t-meta-strong`'s own --fg2 — the
    // accent is for a decision still waiting on the world (decision 7).
    expect(refusal.style.color).toBe("");
  });

  // There is no setting in a fire: it either queued or it did not, and nothing
  // about the trigger changed either way.
  it("says a refused fire queued nothing, rather than talking about a setting", () => {
    renderRow({}, { pending: { kind: "firing", at: QUEUED_AT, error: "gone", status: 404 }, open: true });
    expect(screen.getByText("404 · that did not land · nothing was queued")).toBeInTheDocument();
  });

  // A refusal belongs to the control that caused it, which is the fire — not to
  // the switch, which sent nothing. The switch's own note is for the setting it
  // failed to change, and a fire changes no setting.
  it("hangs a refused fire off the fire button and never off the switch", () => {
    renderRow(
      {},
      { pending: { kind: "firing", at: QUEUED_AT, error: "Service Unavailable", status: 503 }, open: true },
    );
    const refusal = screen.getByText("503 · that did not land · nothing was queued");
    const fire = screen.getByRole("button", { name: "Fire now" });

    // The button no longer promises a queue that never happened: a reader who
    // focuses `Fire now` used to hear `queued only; …`, the opposite of what
    // the server said.
    expect(fire).toHaveAttribute("aria-describedby", refusal.id);
    expect(screen.queryByText(FIRE_NOTE)).toBeNull();
    // And the switch, which sent nothing, describes nothing.
    expect(screen.getByRole("switch")).not.toHaveAttribute("aria-describedby");
  });

  // A refusal is settled, so it takes `.t-meta-strong`'s own `--fg2` — the
  // accent is for a decision still waiting on the world.
  it("keeps the accent for a fire that really was queued", () => {
    const { update } = renderRow({}, { pending: { kind: "firing", at: QUEUED_AT }, firedAt: QUEUED_AT, open: true });
    expect(screen.getByText(/^queued 21:15 · look for/)).toHaveStyle({
      color: "var(--accent-text)",
    });

    update({
      pending: { kind: "firing", at: QUEUED_AT, error: "Service Unavailable", status: 503 },
      firedAt: QUEUED_AT,
      open: true,
    });
    const refusal = screen.getByText("503 · that did not land · nothing was queued");
    expect(refusal.style.color).toBe("");
  });

  // A request that never reached a server has no status, and the row must not
  // print a number nobody sent — so it quotes what it does have.
  it("prints the error itself when no server answered to give a status", () => {
    renderRow(
      { enabled: true },
      { pending: { kind: "enabling", at: QUEUED_AT, error: "Failed to fetch" } },
    );
    expect(
      screen.getByText(
        "Failed to fetch · that did not land · the scheduler still has the old setting",
      ),
    ).toBeInTheDocument();
  });

  // The 60 s is the *enabled-cache* window and says nothing about a manual
  // fire, so there is no third `firing · takes effect within 60 s` sentence.
  it("keeps a queued fire's note under its own button and off the row", () => {
    renderRow({}, { pending: { kind: "firing", at: QUEUED_AT }, open: true });
    expect(screen.queryByText(/takes effect within 60 s/)).toBeNull();
    expect(screen.getByText(FIRE_NOTE)).toBeInTheDocument();
  });

  // A queued fire says nothing about the stored `enabled`, so it must not lock
  // the switch — which is also what stops a collapsed row sitting with an inert
  // control and its only explanation folded away inside the panel.
  it("leaves the switch working while a fire is in flight", () => {
    const { onToggle, row } = renderRow({}, { pending: { kind: "firing", at: QUEUED_AT } });
    const control = screen.getByRole("switch");
    expect(control).not.toHaveAttribute("aria-disabled");
    fireEvent.click(control);
    expect(onToggle).toHaveBeenCalledWith(row);
  });

  // Deviation 6: `GET /api/admin/triggers` drops unparseable records, so the
  // only way to meet one is to act on a record that decayed since the read.
  it("draws the corrupt-record card from a 500, with the server's own detail", () => {
    renderRow(
      {},
      {
        pending: {
          kind: "enabling",
          at: QUEUED_AT,
          error: "condition JSON fails to parse at byte 118",
          status: 500,
        },
        open: true,
      },
    );
    expect(screen.getByText("This record can't be read.")).toBeInTheDocument();
    const card = screen.getByText(
      "500 · condition JSON fails to parse at byte 118 · " +
        "the scheduler skips it · fix in the store or delete",
    );
    expect(screen.getByRole("switch")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("switch")).toHaveAttribute("aria-describedby", card.id);
    expect(screen.getByRole("button", { name: "Fire now" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    // The card replaces the meta line, not the name: a card that does not say
    // which record cannot be read is not worth drawing.
    expect(screen.getByText("Bins out")).toBeInTheDocument();
    expect(
      screen.queryByText("recurring · runs 08:40 tomorrow · created from conversation 20:52"),
    ).toBeNull();
  });

  // The documented way to reach the card is a tap on a *collapsed* row, and a
  // 500 that carried no detail must not leave an empty clause in the sentence.
  it("draws the card on a collapsed row, saying only what the server sent", () => {
    renderRow({}, { pending: { kind: "firing", at: QUEUED_AT, status: 500 } });
    expect(
      screen.getByText("500 · the scheduler skips it · fix in the store or delete"),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-disabled", "true");
  });

  // It is done; it should not read as live — and it should still be readable,
  // which `opacity: .55` is not: composited it takes the meta to 2.71:1 light.
  it("steps a spent one-shot back by token rather than by opacity", () => {
    renderRow({ one_shot: true, last_fired: FIRED_ISO });
    expect(screen.getByRole("listitem").style.opacity).toBe("");
    expect(screen.getByText("Bins out").style.color).toBe("var(--fg2)");
    expect(screen.getByText("time").style.color).toBe("var(--fg2)");
  });

  it("leaves a one-shot that is still waiting at full strength", () => {
    renderRow({ one_shot: true, last_fired: null });
    expect(screen.getByText("Bins out").style.color).toBe("");
    expect(screen.getByText("time").style.color).toBe("var(--accent-text)");
  });

  it("does not step back a recurring trigger that has fired", () => {
    renderRow({ one_shot: false, last_fired: FIRED_ISO });
    expect(screen.getByText("Bins out").style.color).toBe("");
  });

  it("opens on the row body without touching the switch", () => {
    const { onToggleOpen, onToggle, update } = renderRow();
    // `aria-controls` pointing at an id that is not in the document is invalid,
    // so it arrives with the panel it names.
    expect(rowButton()).not.toHaveAttribute("aria-controls");
    fireEvent.click(rowButton());
    expect(onToggleOpen).toHaveBeenCalledWith("trg_bins");
    // The classic defect in this layout: expanding a row must never queue a change.
    expect(onToggle).not.toHaveBeenCalled();
    update({ open: true });
    const panel = openRowButton().getAttribute("aria-controls") ?? "";
    expect(document.getElementById(panel)).not.toBeNull();
  });

  it("holds the detail the meta line leaves out until it is opened", () => {
    const { update } = renderRow({ last_fired: FIRED_ISO });
    const detail = "created by conversation · 20:52 · urgency important · last fired 21:15";
    expect(screen.queryByText(detail)).toBeNull();
    update({ open: true });
    expect(screen.getByText(detail)).toBeInTheDocument();
    expect(openRowButton()).toHaveAttribute("aria-expanded", "true");
  });

  it("says never fired rather than leaving the clause out", () => {
    renderRow({}, { open: true });
    expect(
      screen.getByText("created by conversation · 20:52 · urgency important · never fired"),
    ).toBeInTheDocument();
  });

  it("says --:-- for a stamp it cannot read, rather than a date made of NaN", () => {
    renderRow({ created_at: "whenever" }, { open: true });
    expect(
      screen.getByText("created by conversation · --:-- · urgency important · never fired"),
    ).toBeInTheDocument();
  });

  it("shows the call it would make and the conditions it waits on", () => {
    renderRow({}, { open: true });
    expect(screen.getByTestId("trigger-action")).toHaveTextContent(
      'notify.send { message: "Bins go out tonight" }',
    );
    expect(JSON.parse(screen.getByTestId("trigger-conditions").textContent ?? "")).toEqual({
      cron: null,
      run_at: new Date(2026, 8, 17, 8, 40, 0).toISOString(),
    });
  });

  it("says so plainly when a trigger only notifies", () => {
    renderRow({ action: null }, { open: true });
    expect(screen.getByText("no action · notification only")).toBeInTheDocument();
    expect(screen.queryByTestId("trigger-action")).toBeNull();
  });

  it("offers a fire that claims nothing about what the trigger did", () => {
    const { onFire, row, update } = renderRow({}, { open: true });
    const before = screen.getByRole("button", { name: "Fire now" });
    expect(screen.getByText(FIRE_NOTE)).toHaveClass("t-meta-strong");
    expect(before).toHaveAttribute("aria-describedby", screen.getByText(FIRE_NOTE).id);
    fireEvent.click(before);
    expect(onFire).toHaveBeenCalledTimes(1);
    expect(onFire).toHaveBeenCalledWith(row);

    // Never `Fired`: the events stream is the only thing that knows.
    update({ open: true, firedAt: QUEUED_AT, pending: { kind: "firing", at: QUEUED_AT } });
    const again = screen.getByRole("button", { name: "Fire again" });
    expect(again).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(again);
    expect(onFire).toHaveBeenCalledTimes(1);
    const note = screen.getByText(
      "queued 21:15 · look for trigger.fired on the events stream to know it ran",
    );
    expect(note.style.color).toBe("var(--accent-text)");
  });

  it("keeps reading Fire again once the window has closed", () => {
    renderRow({}, { open: true, firedAt: QUEUED_AT });
    const again = screen.getByRole("button", { name: "Fire again" });
    expect(again).not.toHaveAttribute("aria-disabled");
    expect(again).toHaveClass("h-11");
  });

  it("gives the row and both controls a tap target of at least 44 px", () => {
    renderRow({}, { open: true });
    expect(openRowButton()).toHaveClass("min-h-11");
    // 32 px of switch, 6 px of hit area above and below it.
    expect(screen.getByRole("switch")).toHaveClass("after:-inset-y-1.5");
    expect(screen.getByRole("switch")).toHaveClass("h-8");
    expect(screen.getByRole("button", { name: "Fire now" })).toHaveClass("h-11");
  });
});
