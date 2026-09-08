import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installViewportVars, KEYBOARD_OPEN_PX, keyboardInset, useKeyboardOpen } from "./viewport";

/** A visualViewport a test can drive: a real EventTarget with settable geometry. */
class FakeVisualViewport extends EventTarget {
  height: number;
  offsetTop: number;

  constructor(height: number, offsetTop = 0) {
    super();
    this.height = height;
    this.offsetTop = offsetTop;
  }

  resizeTo(height: number): void {
    this.height = height;
    this.dispatchEvent(new Event("resize"));
  }

  scrollTo(offsetTop: number): void {
    this.offsetTop = offsetTop;
    this.dispatchEvent(new Event("scroll"));
  }
}

const original = window.visualViewport;

function install(viewport: FakeVisualViewport | null): void {
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    writable: true,
    value: viewport,
  });
}

function setInnerHeight(value: number): void {
  Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value });
}

const appHeight = () => document.documentElement.style.getPropertyValue("--app-height");
const keyboard = () => document.documentElement.style.getPropertyValue("--keyboard-inset");

beforeEach(() => setInnerHeight(852));

afterEach(() => {
  install(original as FakeVisualViewport | null);
  document.documentElement.style.removeProperty("--app-height");
  document.documentElement.style.removeProperty("--keyboard-inset");
});

describe("installViewportVars", () => {
  it("mirrors the visual viewport onto the document element", () => {
    install(new FakeVisualViewport(852));
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("852px");
    expect(keyboard()).toBe("0px");

    uninstall();
  });

  it("reports the keyboard inset when the viewport shrinks", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.resizeTo(500);

    expect(appHeight()).toBe("500px");
    expect(keyboard()).toBe("352px");

    uninstall();
  });

  it("follows the viewport when it is scrolled under the keyboard", () => {
    const viewport = new FakeVisualViewport(500);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.scrollTo(60);

    // 852 - 500 - 60: the offset is part of what is hidden.
    expect(keyboard()).toBe("292px");

    uninstall();
  });

  it("rounds fractional heights to whole pixels", () => {
    const viewport = new FakeVisualViewport(500.4);
    install(viewport);
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("500px");

    uninstall();
  });

  it("stops updating once uninstalled", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();
    uninstall();

    viewport.resizeTo(400);

    expect(appHeight()).toBe("852px");
  });

  it("falls back to innerHeight where there is no visual viewport", () => {
    install(null);
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("852px");
    expect(keyboard()).toBe("0px");
    expect(keyboardInset()).toBe(0);

    uninstall();
  });

  it("never reports a negative inset", () => {
    // iOS briefly reports a viewport taller than the window during rotation.
    install(new FakeVisualViewport(900));
    const uninstall = installViewportVars();

    expect(keyboard()).toBe("0px");

    uninstall();
  });
});

describe("useKeyboardOpen", () => {
  it("is the 80 px threshold, not any inset at all", () => {
    expect(KEYBOARD_OPEN_PX).toBe(80);
  });

  it("flips as the viewport crosses the threshold", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);

    const { result } = renderHook(() => useKeyboardOpen());
    expect(result.current).toBe(false);

    act(() => viewport.resizeTo(772)); // inset exactly 80 — not open
    expect(result.current).toBe(false);

    act(() => viewport.resizeTo(771)); // inset 81 — open
    expect(result.current).toBe(true);

    act(() => viewport.resizeTo(852));
    expect(result.current).toBe(false);
  });
});
