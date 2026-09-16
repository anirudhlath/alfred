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

/** One row in the list it lives in, as `RoutineRow.test.tsx` renders its own. */
function renderRow(
  overrides: Partial<Trigger> = {},
  props: { pending?: Pending; firedAt?: number; open?: boolean } = {},
) {
  const onToggleOpen = vi.fn();
  const onToggle = vi.fn();
  const onFire = vi.fn();
  const row = trigger(overrides);
  const { rerender } = render(
    <ul>
      <TriggerRow
        trigger={row}
        now={TRIGGER_NOW}
        pending={props.pending}
        firedAt={props.firedAt}
        open={props.open ?? false}
        onToggleOpen={onToggleOpen}
        onToggle={onToggle}
        onFire={onFire}
      />
    </ul>,
  );
  return {
    row,
    onToggleOpen,
    onToggle,
    onFire,
    /** Re-render the same row with a later state, the way the hook would. */
    update: (next: { pending?: Pending; firedAt?: number; open?: boolean }) =>
      rerender(
        <ul>
          <TriggerRow
            trigger={row}
            now={TRIGGER_NOW}
            pending={next.pending}
            firedAt={next.firedAt}
            open={next.open ?? false}
            onToggleOpen={onToggleOpen}
            onToggle={onToggle}
            onFire={onFire}
          />
        </ul>,
      ),
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
    expect(screen.getByRole("switch")).not.toHaveAttribute("aria-busy", "true");
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
    expect(control).toBeDisabled();
    // The knob keeps the off position; the note carries the wanted state.
    expect(knob().style.left).toBe("3px");
    fireEvent.click(control);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("says when the change was queued and how long it may take", () => {
    renderRow({ enabled: false }, { pending: { kind: "enabling", at: QUEUED_AT } });
    const note = screen.getByText("queued 21:15 · enabling · takes effect within 60 s");
    expect(note.style.color).toBe("var(--accent-text)");
    expect(note).toHaveClass("t-meta-strong");
  });

  it("says disabling for a change in the other direction", () => {
    renderRow({ enabled: true }, { pending: { kind: "disabling", at: QUEUED_AT } });
    expect(
      screen.getByText("queued 21:15 · disabling · takes effect within 60 s"),
    ).toBeInTheDocument();
    expect(knob().style.left).toBe("23px");
  });

  it("says a refused change did not land, and that the old setting stands", () => {
    renderRow(
      { enabled: true },
      { pending: { kind: "disabling", at: QUEUED_AT, error: "Trigger not found", status: 404 } },
    );
    expect(
      screen.getByText("404 · that did not land · the scheduler still has the old setting"),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  // A request that never reached a server has no status, and the row must not
  // print a number nobody sent — so it quotes what it does have.
  it("prints the error itself when no server answered to give a status", () => {
    renderRow({ enabled: true }, { pending: { kind: "firing", at: QUEUED_AT, error: "Failed to fetch" } });
    expect(
      screen.getByText("Failed to fetch · that did not land · the scheduler still has the old setting"),
    ).toBeInTheDocument();
  });

  // Deviation 6: `GET /api/admin/triggers` drops unparseable records, so the
  // only way to meet one is to toggle a record that decayed since the read.
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
    expect(
      screen.getByText(
        "500 · condition JSON fails to parse at byte 118 · the scheduler skips it · fix in the store or delete",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Fire now" })).toBeDisabled();
    // The card replaces the meta line, not the name: a card that does not say
    // which record cannot be read is not worth drawing.
    expect(screen.getByText("Bins out")).toBeInTheDocument();
    expect(
      screen.queryByText("recurring · runs 08:40 tomorrow · created from conversation 20:52"),
    ).toBeNull();
  });

  // It is done, and it should not read as live.
  it("dims a one-shot that has already fired", () => {
    renderRow({ one_shot: true, last_fired: new Date(2026, 8, 16, 21, 15, 0).toISOString() });
    expect(screen.getByRole("listitem").style.opacity).toBe("0.55");
  });

  it("leaves a one-shot that is still waiting at full strength", () => {
    renderRow({ one_shot: true, last_fired: null });
    expect(screen.getByRole("listitem").style.opacity).toBe("");
  });

  it("does not dim a recurring trigger that has fired", () => {
    renderRow({ one_shot: false, last_fired: new Date(2026, 8, 16, 21, 15, 0).toISOString() });
    expect(screen.getByRole("listitem").style.opacity).toBe("");
  });

  it("opens on the row body without touching the switch", () => {
    const { onToggleOpen, onToggle } = renderRow();
    fireEvent.click(rowButton());
    expect(onToggleOpen).toHaveBeenCalledWith("trg_bins");
    // The classic defect in this layout: expanding a row must never queue a change.
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("holds the detail the meta line leaves out until it is opened", () => {
    const { update } = renderRow({
      last_fired: new Date(2026, 8, 16, 21, 15, 0).toISOString(),
    });
    expect(
      screen.queryByText("created by conversation · 20:52 earlier today · urgency important · last fired 21:15 earlier today"),
    ).toBeNull();
    update({ open: true });
    expect(
      screen.getByText(
        "created by conversation · 20:52 earlier today · urgency important · last fired 21:15 earlier today",
      ),
    ).toBeInTheDocument();
    expect(openRowButton()).toHaveAttribute("aria-expanded", "true");
  });

  it("says never fired rather than leaving the clause out", () => {
    renderRow({}, { open: true });
    expect(
      screen.getByText(
        "created by conversation · 20:52 earlier today · urgency important · never fired",
      ),
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
    expect(screen.getByText(FIRE_NOTE)).toHaveClass("t-meta-strong");
    fireEvent.click(screen.getByRole("button", { name: "Fire now" }));
    expect(onFire).toHaveBeenCalledTimes(1);
    expect(onFire).toHaveBeenCalledWith(row);

    // Never `Fired`: the events stream is the only thing that knows.
    update({ open: true, firedAt: QUEUED_AT, pending: { kind: "firing", at: QUEUED_AT } });
    const again = screen.getByRole("button", { name: "Fire again" });
    expect(again).toBeDisabled();
    const note = screen.getByText(
      "queued 21:15 · look for trigger.fired on the events stream to know it ran",
    );
    expect(note.style.color).toBe("var(--accent-text)");
    expect(screen.queryByText("Fired")).toBeNull();
  });

  it("keeps reading Fire again once the window has closed", () => {
    renderRow({}, { open: true, firedAt: QUEUED_AT });
    const again = screen.getByRole("button", { name: "Fire again" });
    expect(again).toBeEnabled();
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
