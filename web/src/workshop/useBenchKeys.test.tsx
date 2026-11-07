import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { STREAMS } from "@/lib/streams";
import { isEditing } from "@/shell/keys";
import type { Activity } from "./useActivity";
import { useBenchKeys } from "./useBenchKeys";

type Keyed = Pick<Activity, "paused" | "solo" | "setSolo" | "pause" | "resume">;

function activity(overrides: Partial<Keyed> = {}): Activity {
  const fake: Keyed = {
    paused: false,
    solo: null,
    setSolo: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    ...overrides,
  };
  return fake as Activity;
}

interface BenchProps {
  a: Activity;
  enabled?: boolean;
  onRow?: () => void;
  onOther?: () => void;
  onAll?: () => void;
}

/** A list of row buttons, one dimmed, with the other things focus can rest on around it. */
function Bench({ a, enabled = true, onRow, onOther, onAll }: BenchProps) {
  const list = useRef<HTMLUListElement>(null);
  useBenchKeys(a, list, enabled);
  return (
    <div data-testid="wrapper">
      <input aria-label="field" />
      <button type="button" onClick={onOther}>
        other
      </button>
      {/* `All` at width with no solo: focusable, and its press does nothing. */}
      <button type="button" aria-disabled="true" onClick={onAll}>
        All
      </button>
      <a href="#there">link</a>
      <div role="tab" tabIndex={0}>
        tab
      </div>
      <div role="switch" aria-checked="false" tabIndex={0}>
        switch
      </div>
      <ul ref={list}>
        <li>
          <button type="button">older</button>
        </li>
        <li>
          <button type="button" data-event-row="" onClick={onRow}>
            one
          </button>
        </li>
        <li>
          <button type="button" data-event-row="" data-dimmed="">
            dimmed
          </button>
        </li>
        <li>
          <button type="button" data-event-row="">
            two
          </button>
          <button type="button">Only RX</button>
        </li>
        <li>
          <button type="button" data-event-row="">
            three
          </button>
          <button type="button">Only EV</button>
        </li>
      </ul>
    </div>
  );
}

const button = (name: string) => screen.getByRole("button", { name });

/** Whether the key was claimed from the browser, which `user-event` has no way to say. */
const claimed = (key: string, init: KeyboardEventInit = {}, target: Element = document.body) =>
  !fireEvent.keyDown(target, { key, ...init });

const spaceDown = (target: Element, init: KeyboardEventInit = {}) => claimed(" ", init, target);
const spaceUp = (target: Element) => !fireEvent.keyUp(target, { key: " " });

describe("useBenchKeys", () => {
  describe("J and K", () => {
    it("J focuses the first row, then the next; K goes back; neither wraps", async () => {
      const user = userEvent.setup();
      render(<Bench a={activity()} />);

      await user.keyboard("j");
      expect(button("one")).toHaveFocus();
      await user.keyboard("k");
      expect(button("one")).toHaveFocus();

      await user.keyboard("j");
      await user.keyboard("j");
      expect(button("three")).toHaveFocus();
      await user.keyboard("j");
      expect(button("three")).toHaveFocus();

      await user.keyboard("k");
      expect(button("two")).toHaveFocus();
    });

    it("J skips dimmed rows", async () => {
      const user = userEvent.setup();
      render(<Bench a={activity()} />);

      await user.keyboard("jj");

      expect(button("dimmed")).not.toHaveFocus();
      expect(button("two")).toHaveFocus();
    });

    it("starts from the first row wherever else focus is", async () => {
      const user = userEvent.setup();
      render(<Bench a={activity()} />);

      button("other").focus();
      await user.keyboard("j");
      expect(button("one")).toHaveFocus();

      button("other").focus();
      await user.keyboard("k");
      expect(button("one")).toHaveFocus();
    });

    it("from a pill in an open row, J goes to the next row and K to the row it sits in", async () => {
      const user = userEvent.setup();
      render(<Bench a={activity()} />);

      button("Only RX").focus();
      await user.keyboard("j");
      expect(button("three")).toHaveFocus();

      button("Only RX").focus();
      await user.keyboard("k");
      expect(button("two")).toHaveFocus();
    });

    it("from a row a solo has just dimmed, J and K go to their nearest undimmed neighbours", async () => {
      const user = userEvent.setup();
      render(<Bench a={activity()} />);

      button("dimmed").focus();
      await user.keyboard("j");
      expect(button("two")).toHaveFocus();

      button("dimmed").focus();
      await user.keyboard("k");
      expect(button("one")).toHaveFocus();
    });

    it("does not wrap from inside the list either", async () => {
      const user = userEvent.setup();
      render(<Bench a={activity()} />);

      // Below the last row, with nothing further down.
      button("Only EV").focus();
      await user.keyboard("j");
      expect(button("Only EV")).toHaveFocus();

      // Above the first, with nothing further up, and the first row one J away.
      button("older").focus();
      await user.keyboard("k");
      expect(button("older")).toHaveFocus();
      await user.keyboard("j");
      expect(button("one")).toHaveFocus();
    });

    it("takes either case, and claims the key", () => {
      render(<Bench a={activity()} />);

      expect(fireEvent.keyDown(document.body, { key: "J", shiftKey: true })).toBe(false);
      expect(button("one")).toHaveFocus();
      expect(fireEvent.keyDown(document.body, { key: "J", shiftKey: true })).toBe(false);
      expect(button("two")).toHaveFocus();
      expect(fireEvent.keyDown(document.body, { key: "K", shiftKey: true })).toBe(false);
      expect(button("one")).toHaveFocus();
    });

    it("Enter on a focused row presses it", async () => {
      const user = userEvent.setup();
      const onRow = vi.fn();
      render(<Bench a={activity()} onRow={onRow} />);

      await user.keyboard("j");
      await user.keyboard("{Enter}");

      expect(onRow).toHaveBeenCalledTimes(1);
    });
  });

  describe("Space", () => {
    it("Space pauses from a row and from nothing, without pressing the row", async () => {
      const user = userEvent.setup();
      const onRow = vi.fn();
      const a = activity();
      render(<Bench a={a} onRow={onRow} />);

      await user.keyboard(" ");
      expect(a.pause).toHaveBeenCalledTimes(1);

      await user.keyboard("j");
      expect(button("one")).toHaveFocus();
      await user.keyboard(" ");

      expect(a.pause).toHaveBeenCalledTimes(2);
      expect(onRow).not.toHaveBeenCalled();
    });

    it("Space resumes when paused", async () => {
      const user = userEvent.setup();
      const a = activity({ paused: true });
      render(<Bench a={a} />);

      await user.keyboard(" ");

      expect(a.resume).toHaveBeenCalledTimes(1);
      expect(a.pause).not.toHaveBeenCalled();
    });

    it("Space on another button is that button's", async () => {
      const user = userEvent.setup();
      const onOther = vi.fn();
      const a = activity();
      render(<Bench a={a} onOther={onOther} />);

      button("other").focus();
      await user.keyboard(" ");

      expect(onOther).toHaveBeenCalledTimes(1);
      expect(a.pause).not.toHaveBeenCalled();
    });

    it("Space on All with no solo is All's, not a pause", async () => {
      const user = userEvent.setup();
      const onAll = vi.fn();
      const a = activity();
      render(<Bench a={a} onAll={onAll} />);

      button("All").focus();
      await user.keyboard(" ");

      expect(onAll).toHaveBeenCalledTimes(1);
      expect(a.pause).not.toHaveBeenCalled();
    });

    it("leaves Space alone on a link, a tab and a switch", () => {
      const a = activity();
      render(<Bench a={a} />);

      for (const control of [
        screen.getByRole("link"),
        screen.getByRole("tab"),
        screen.getByRole("switch"),
      ]) {
        expect(spaceDown(control)).toBe(false);
      }
      expect(a.pause).not.toHaveBeenCalled();
    });

    it("holding Space toggles once", () => {
      const a = activity();
      render(<Bench a={a} />);

      spaceDown(document.body);
      // The repeats are claimed too, or holding it would scroll the page.
      expect(spaceDown(document.body, { repeat: true })).toBe(true);
      expect(spaceDown(document.body, { repeat: true })).toBe(true);

      expect(a.pause).toHaveBeenCalledTimes(1);
    });

    it("stops a row's Space clicking it on keyup, which Firefox does regardless", () => {
      render(<Bench a={activity()} />);

      expect(spaceDown(button("one"))).toBe(true);

      expect(spaceUp(button("one"))).toBe(true);
    });

    it("still claims the keyup when the press re-rendered the bench", () => {
      // The press itself is what hands the hook a new `activity`.
      const { rerender } = render(<Bench a={activity()} />);

      spaceDown(button("one"));
      rerender(<Bench a={activity({ paused: true })} />);

      expect(spaceUp(button("one"))).toBe(true);
    });

    it("leaves a keyup after a Space on another button to that button", () => {
      render(<Bench a={activity()} />);

      spaceDown(button("other"));

      expect(spaceUp(button("other"))).toBe(false);
    });

    it("claims only the one keyup that matches", () => {
      render(<Bench a={activity()} />);

      spaceDown(document.body);
      expect(fireEvent.keyUp(document.body, { key: "j" })).toBe(true);
      expect(spaceUp(document.body)).toBe(true);

      expect(spaceUp(document.body)).toBe(false);
    });

    it("forgets a keyup that never came, so the next button keeps its own", () => {
      // The window lost focus with Space down, and the keyup went elsewhere.
      render(<Bench a={activity()} />);

      spaceDown(document.body);
      spaceDown(button("other"));

      expect(spaceUp(button("other"))).toBe(false);
    });
  });

  describe("1 to 8", () => {
    it("1 solos the first stream, 1 again clears, 9 does nothing", async () => {
      const user = userEvent.setup();
      const a = activity();
      const { rerender } = render(<Bench a={a} />);

      await user.keyboard("1");
      expect(a.setSolo).toHaveBeenLastCalledWith(STREAMS[0]);

      const soloed = activity({ solo: STREAMS[0] });
      rerender(<Bench a={soloed} />);
      await user.keyboard("1");
      expect(soloed.setSolo).toHaveBeenLastCalledWith(null);

      const other = activity();
      rerender(<Bench a={other} />);
      await user.keyboard("9");
      await user.keyboard("0");
      expect(other.setSolo).not.toHaveBeenCalled();
    });

    it("8 solos the last stream, and another digit moves the solo", async () => {
      const user = userEvent.setup();
      const a = activity({ solo: STREAMS[0] });
      render(<Bench a={a} />);

      await user.keyboard("8");

      expect(a.setSolo).toHaveBeenCalledWith(STREAMS[7]);
    });

    it("does not flicker while a digit is held, and still claims it", () => {
      const a = activity();
      render(<Bench a={a} />);

      expect(claimed("1", { repeat: true })).toBe(true);

      expect(a.setSolo).not.toHaveBeenCalled();
    });

    it("claims 1 to 8 and leaves 9 and 0 alone", () => {
      const a = activity();
      render(<Bench a={a} />);

      for (const key of ["1", "2", "3", "4", "5", "6", "7", "8"]) expect(claimed(key)).toBe(true);
      for (const key of ["9", "0"]) expect(claimed(key)).toBe(false);
    });
  });

  describe("when it stays silent", () => {
    function expectSilent(a: Activity): void {
      expect(a.pause).not.toHaveBeenCalled();
      expect(a.resume).not.toHaveBeenCalled();
      expect(a.setSolo).not.toHaveBeenCalled();
      for (const name of ["one", "two", "three"]) expect(button(name)).not.toHaveFocus();
    }

    it("is silent in a field", async () => {
      const user = userEvent.setup();
      const a = activity();
      render(<Bench a={a} />);
      const field = screen.getByLabelText("field");

      await user.type(field, "j 1");

      expect(field).toHaveValue("j 1");
      expect(field).toHaveFocus();
      for (const key of ["j", " ", "1"]) expect(claimed(key, {}, field)).toBe(false);
      expectSilent(a);
    });

    it.each([
      ["ctrl", { ctrlKey: true }],
      ["meta", { metaKey: true }],
      ["alt", { altKey: true }],
    ])("is silent with %s", (_name, modifier) => {
      const a = activity();
      render(<Bench a={a} />);

      for (const key of ["j", "k", " ", "1"]) {
        // Not claimed either: these belong to the browser and the OS.
        expect(fireEvent.keyDown(document.body, { key, ...modifier })).toBe(true);
      }

      expectSilent(a);
    });

    it("is silent while composing", () => {
      const a = activity();
      render(<Bench a={a} />);

      for (const key of ["j", " ", "1"]) {
        expect(fireEvent.keyDown(document.body, { key, isComposing: true })).toBe(true);
      }

      expectSilent(a);
    });

    it("is silent under an inert ancestor", () => {
      const a = activity();
      render(<Bench a={a} />);
      screen.getByTestId("wrapper").setAttribute("inert", "");

      for (const key of ["j", " ", "1"]) {
        expect(fireEvent.keyDown(document.body, { key })).toBe(true);
      }

      expectSilent(a);
    });

    it("is silent when not enabled", () => {
      const a = activity();
      render(<Bench a={a} enabled={false} />);

      for (const key of ["j", " ", "1"]) {
        expect(fireEvent.keyDown(document.body, { key })).toBe(true);
      }

      expectSilent(a);
    });

    it("takes its listeners with it when the width goes", () => {
      const a = activity();
      const { rerender } = render(<Bench a={a} />);
      spaceDown(document.body);
      vi.mocked(a.pause).mockClear();

      // The list stays, so a listener left behind would still have rows to act on.
      rerender(<Bench a={a} enabled={false} />);

      // And a keyup one would still claim the Space it saw go down.
      expect(spaceUp(document.body)).toBe(false);
      for (const key of ["j", " ", "1"]) {
        expect(fireEvent.keyDown(document.body, { key })).toBe(true);
      }
      expectSilent(a);
    });
  });
});

describe("isEditing", () => {
  const made = (html: string) => {
    const host = document.createElement("div");
    host.innerHTML = html;
    return host.firstElementChild as Element;
  };

  it.each([
    ["an input", "<input />"],
    ["a textarea", "<textarea></textarea>"],
    ["a select", "<select></select>"],
    ["a contenteditable", '<div contenteditable="true"></div>'],
  ])("says yes for %s", (_name, html) => {
    expect(isEditing(made(html))).toBe(true);
  });

  it("says yes inside a contenteditable", () => {
    expect(isEditing(made('<div contenteditable="true"><b>bold</b></div>').firstElementChild)).toBe(true);
  });

  it("says no for a button, the body, the document and nothing", () => {
    expect(isEditing(made("<button></button>"))).toBe(false);
    expect(isEditing(document.body)).toBe(false);
    expect(isEditing(document)).toBe(false);
    expect(isEditing(null)).toBe(false);
  });
});
