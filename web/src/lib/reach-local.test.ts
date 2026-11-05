import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hhmm } from "./format";
import {
  addReachAct,
  gateAnswered,
  markGateAnswered,
  reachActRow,
  readAnsweredDoors,
  readReachActs,
  writeAnsweredDoors,
} from "./reach-local";

const AT = "2026-11-04T07:44:00.000Z";
const LATER = "2026-11-04T07:50:00.000Z";

// The names themselves, not imports: a rename would lose what phones already hold.
const ACTS = "alfred.reach.acts";
const DOORS = "alfred.reach.answeredDoors";
const GATE = "alfred.reach.gateAnswered";

/** The store refuses, as a full or forbidden one does: `Storage.prototype`, the only spy jsdom's store obeys. */
function refuse(method: "getItem" | "setItem"): void {
  vi.spyOn(Storage.prototype, method).mockImplementation(() => {
    throw new DOMException("refused", "QuotaExceededError");
  });
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("the Act rows", () => {
  it("reachActRow on", () => {
    expect(reachActRow({ at: AT, outcome: "on" })).toMatchObject({
      kind: "act",
      at: AT,
      hue: 255,
      text: "Notifications on for this iPhone",
      meta: `${hhmm(AT)} · notifications · iOS allowed · important and urgent only`,
    });
  });

  it("reachActRow off", () => {
    expect(reachActRow({ at: AT, outcome: "off" })).toMatchObject({
      kind: "act",
      at: AT,
      hue: 255,
      text: "Notifications stay off on this iPhone",
      meta: `${hhmm(AT)} · iOS declined · Settings › Alfred › Notifications to change · not asked again`,
    });
  });
});

describe("what this device keeps", () => {
  it("each list survives a reload", async () => {
    addReachAct("off", AT);
    addReachAct("on", LATER);
    writeAnsweredDoors(["req-1", "req-2"]);
    markGateAnswered();

    // A fresh copy of the module: nothing it holds in memory can answer.
    vi.resetModules();
    const reloaded = await import("./reach-local");

    expect(reloaded.readReachActs()).toEqual([
      { at: AT, outcome: "off" },
      { at: LATER, outcome: "on" },
    ]);
    expect(reloaded.readAnsweredDoors()).toEqual(["req-1", "req-2"]);
    expect(reloaded.gateAnswered()).toBe(true);
  });

  it("starts empty", () => {
    expect(readReachActs()).toEqual([]);
    expect(readAnsweredDoors()).toEqual([]);
    expect(gateAnswered()).toBe(false);
  });
});

describe("corrupt storage reads as empty", () => {
  it.each([
    "{",
    "null",
    '"on"',
    '{"outcome":"on"}',
    '[{"outcome":"bad","at":"x"}]',
    `[{"outcome":"bad","at":"${AT}"}]`,
    '[{"outcome":"on","at":"not a time"}]',
    '[{"outcome":"on"}]',
    '[1,null,"on",[]]',
  ])("acts: %s", (stored) => {
    localStorage.setItem(ACTS, stored);
    expect(readReachActs()).toEqual([]);
  });

  it("keeps a good act beside a bad one", () => {
    localStorage.setItem(ACTS, JSON.stringify([{ outcome: "bad", at: "x" }, { at: AT, outcome: "on" }, 7]));
    expect(readReachActs()).toEqual([{ at: AT, outcome: "on" }]);
  });

  it.each(["{", "null", '"req-1"', '{"id":"req-1"}'])("doors: %s", (stored) => {
    localStorage.setItem(DOORS, stored);
    expect(readAnsweredDoors()).toEqual([]);
  });

  it("drops a door that is not a string", () => {
    localStorage.setItem(DOORS, JSON.stringify(["req-1", 7, null, { id: "req-2" }, "req-3"]));
    expect(readAnsweredDoors()).toEqual(["req-1", "req-3"]);
  });

  it("reads the gate as unanswered on anything but its own mark", () => {
    localStorage.setItem(GATE, "true");
    expect(gateAnswered()).toBe(false);
  });

  it("writes over what it could not read", () => {
    localStorage.setItem(ACTS, "{");
    expect(addReachAct("on", AT)).toEqual([{ at: AT, outcome: "on" }]);
    expect(readReachActs()).toEqual([{ at: AT, outcome: "on" }]);
  });
});

describe("a store that throws", () => {
  it("a throwing store loses the facts, never the app", () => {
    refuse("getItem");
    refuse("setItem");

    expect(addReachAct("on", AT)).toEqual([{ at: AT, outcome: "on" }]);
    // The store's word, not the row's: only what the caller was handed has it now.
    expect(readReachActs()).toEqual([]);

    expect(() => writeAnsweredDoors(["req-1"])).not.toThrow();
    expect(readAnsweredDoors()).toEqual([]);

    expect(() => markGateAnswered()).not.toThrow();
    expect(gateAnswered()).toBe(false);
  });

  it("builds on the store when nothing is held and only the write throws", () => {
    addReachAct("off", AT);
    refuse("setItem");

    expect(addReachAct("on", LATER)).toEqual([
      { at: AT, outcome: "off" },
      { at: LATER, outcome: "on" },
    ]);
    // The second answer is only in the list it returned, for the caller to hold.
    expect(readReachActs()).toEqual([{ at: AT, outcome: "off" }]);
  });

  it("keeps two answers the store refused, when the first is handed back", () => {
    refuse("setItem");

    const first = addReachAct("off", AT);
    expect(addReachAct("on", LATER, first)).toEqual([
      { at: AT, outcome: "off" },
      { at: LATER, outcome: "on" },
    ]);
    expect(readReachActs()).toEqual([]);
  });
});

describe("what addReachAct builds on", () => {
  it("is the list it is given, over what the store holds", () => {
    addReachAct("off", AT);

    expect(addReachAct("on", LATER, [])).toEqual([{ at: LATER, outcome: "on" }]);
    expect(readReachActs()).toEqual([{ at: LATER, outcome: "on" }]);
  });

  it("stores a refused answer once the store takes writes again", () => {
    const refusing = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("refused", "QuotaExceededError");
    });
    const first = addReachAct("off", AT);
    refusing.mockRestore();

    addReachAct("on", LATER, first);
    expect(readReachActs()).toEqual([
      { at: AT, outcome: "off" },
      { at: LATER, outcome: "on" },
    ]);
  });
});
