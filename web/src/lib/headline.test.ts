import { describe, expect, it } from "vitest";
import { greetingFor, pickHeadline, type HeadlineInput } from "./headline";

const idle: HeadlineInput = {
  online: true,
  reconnecting: false,
  firstRun: false,
  dnd: { active: false },
  holding: false,
  busy: false,
  hour: 21,
};

describe("pickHeadline", () => {
  it("listens when there is nothing else to say", () => {
    expect(pickHeadline(idle)).toBe("Listening, sir.");
  });

  it("says the house is unreachable before anything else", () => {
    expect(
      pickHeadline({
        ...idle,
        online: false,
        holding: true,
        busy: true,
        firstRun: true,
        dnd: { active: true, until: "2026-09-08T08:30:00" },
      }),
    ).toBe("Unreachable.");
  });

  it("distinguishes reconnecting from gone", () => {
    expect(pickHeadline({ ...idle, online: false, reconnecting: true })).toBe("Reconnecting…");
  });

  it("attends to the microphone over everything the house is doing", () => {
    expect(pickHeadline({ ...idle, holding: true, busy: true })).toBe("Go on, sir.");
  });

  it("says one moment while a turn is in flight", () => {
    expect(pickHeadline({ ...idle, busy: true })).toBe("One moment, sir.");
  });

  it("names the hour do-not-disturb ends", () => {
    expect(
      pickHeadline({ ...idle, dnd: { active: true, until: new Date(2026, 8, 8, 8, 30).toISOString() } }),
    ).toBe("Quiet until 08:30.");
  });

  it("says so when do-not-disturb has no end at all", () => {
    expect(pickHeadline({ ...idle, dnd: { active: true, until: null } })).toBe(
      "Quiet until further notice.",
    );
    expect(pickHeadline({ ...idle, dnd: { active: true } })).toBe("Quiet until further notice.");
  });

  it("refuses to invent an expiry from an unparseable one", () => {
    expect(pickHeadline({ ...idle, dnd: { active: true, until: "soon" } })).toBe(
      "Quiet until further notice.",
    );
  });

  it("greets by the hour on a first run", () => {
    const greet = (hour: number) => pickHeadline({ ...idle, firstRun: true, hour });
    expect(greet(5)).toBe("Good morning, sir.");
    expect(greet(11)).toBe("Good morning, sir.");
    expect(greet(12)).toBe("Good afternoon, sir.");
    expect(greet(17)).toBe("Good afternoon, sir.");
    expect(greet(18)).toBe("Good evening, sir.");
    expect(greet(23)).toBe("Good evening, sir.");
    expect(greet(4)).toBe("Good evening, sir.");
  });

  it("prefers quiet to a greeting", () => {
    expect(pickHeadline({ ...idle, firstRun: true, dnd: { active: true, until: null } })).toBe(
      "Quiet until further notice.",
    );
  });
});

describe("greetingFor", () => {
  it("is the same table the headline uses, exposed for the first-day row", () => {
    expect(greetingFor(9)).toBe("Good morning, sir.");
    expect(greetingFor(14)).toBe("Good afternoon, sir.");
    expect(greetingFor(21)).toBe("Good evening, sir.");
  });
});
