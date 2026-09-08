import { afterEach, describe, expect, it, vi } from "vitest";
import { applyTheme, rememberTheme, resolveInitialTheme, storedTheme, THEME_KEY } from "./theme";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("resolveInitialTheme", () => {
  it("resolves the stored theme whatever the hour", () => {
    expect(resolveInitialTheme(new Date(2026, 8, 7, 12, 0), "dark")).toBe("dark");
    expect(resolveInitialTheme(new Date(2026, 8, 7, 23, 0), "light")).toBe("light");
  });

  it("ignores a stored value that is not a theme", () => {
    expect(resolveInitialTheme(new Date(2026, 8, 7, 12, 0), "banana")).toBe("light");
    expect(resolveInitialTheme(new Date(2026, 8, 7, 23, 0), "")).toBe("dark");
  });

  it("is light from 07:00 to 18:59 and dark otherwise", () => {
    const at = (hour: number) => resolveInitialTheme(new Date(2026, 8, 7, hour, 30), null);
    expect(at(0)).toBe("dark");
    expect(at(6)).toBe("dark");
    expect(at(7)).toBe("light");
    expect(at(12)).toBe("light");
    expect(at(18)).toBe("light");
    expect(at(19)).toBe("dark");
    expect(at(23)).toBe("dark");
  });
});

describe("applyTheme", () => {
  it("writes data-theme on the document element, and nothing to storage", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("tints Safari's chrome to match", () => {
    // index.html ships the meta; the test page does not, so make one.
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    meta.content = "#25221F";
    document.head.append(meta);
    try {
      applyTheme("light");
      expect(meta.content).toBe("#F6F3EE");
      applyTheme("dark");
      expect(meta.content).toBe("#25221F");
    } finally {
      meta.remove();
    }
  });

  it("does not need the meta to exist", () => {
    expect(() => applyTheme("light")).not.toThrow();
  });
});

describe("rememberTheme", () => {
  it("uses the alfred.theme key", () => {
    expect(THEME_KEY).toBe("alfred.theme");
  });

  it("persists the choice", () => {
    rememberTheme("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("survives a storage that refuses to write", () => {
    vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => rememberTheme("light")).not.toThrow();
  });
});

describe("storedTheme", () => {
  it("is null before anything is stored", () => {
    expect(storedTheme()).toBeNull();
  });

  it("reads back what rememberTheme wrote", () => {
    rememberTheme("light");
    expect(storedTheme()).toBe("light");
  });

  it("is null when storage cannot be read", () => {
    vi.spyOn(localStorage, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(storedTheme()).toBeNull();
  });
});
