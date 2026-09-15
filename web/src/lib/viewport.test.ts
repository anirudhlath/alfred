import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installViewportVars, KEYBOARD_OPEN_PX, keyboardInset, useKeyboardOpen } from "./viewport";

/** A visualViewport a test can drive: a real EventTarget with settable geometry. */
class FakeVisualViewport extends EventTarget {
  height: number;
  offsetTop: number;
  scale: number;

  constructor(height: number, offsetTop = 0, scale = 1) {
    super();
    this.height = height;
    this.offsetTop = offsetTop;
    this.scale = scale;
  }

  /** What a pinch or an iOS focus-zoom does: the same band, fewer page pixels. */
  zoomTo(scale: number): void {
    this.height = Math.round((window.innerHeight / scale) * 100) / 100;
    this.scale = scale;
    this.dispatchEvent(new Event("resize"));
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

function resizeWindowTo(value: number): void {
  setInnerHeight(value);
  window.dispatchEvent(new Event("resize"));
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
  it("mirrors the window onto the document element", () => {
    install(new FakeVisualViewport(852));
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("852px");
    expect(keyboard()).toBe("0px");

    uninstall();
  });

  it("reports the keyboard inset when the viewport shrinks, and keeps the column", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.resizeTo(500);

    // .pb-keyboard pays for the keyboard; a shorter column would pay for it twice.
    expect(keyboard()).toBe("352px");
    expect(appHeight()).toBe("852px");

    uninstall();
  });

  it("follows the viewport when it is scrolled under the keyboard", () => {
    const viewport = new FakeVisualViewport(500);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.scrollTo(60);

    // 852 - 500 - 60: the offset is part of what is hidden.
    expect(keyboard()).toBe("292px");
    expect(appHeight()).toBe("852px");

    uninstall();
  });

  it("rounds the inset to whole pixels", () => {
    const viewport = new FakeVisualViewport(500.4);
    install(viewport);
    const uninstall = installViewportVars();

    // 852 - 500.4 = 351.6
    expect(keyboard()).toBe("352px");

    uninstall();
  });

  it("follows window resizes, with or without a visual viewport", () => {
    install(null);
    let uninstall = installViewportVars();

    resizeWindowTo(400);

    expect(appHeight()).toBe("400px");
    expect(keyboard()).toBe("0px");

    uninstall();
    install(new FakeVisualViewport(300));
    uninstall = installViewportVars();

    resizeWindowTo(300);

    expect(appHeight()).toBe("300px");
    expect(keyboard()).toBe("0px");

    uninstall();
  });

  it("stops updating once uninstalled", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();
    uninstall();

    viewport.resizeTo(500);
    expect(keyboard()).toBe("0px");

    viewport.scrollTo(60);
    expect(keyboard()).toBe("0px");

    resizeWindowTo(400);
    expect(appHeight()).toBe("852px");
  });

  it("reports no keyboard where there is no visual viewport", () => {
    install(null);
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("852px");
    expect(keyboard()).toBe("0px");
    expect(keyboardInset()).toBe(0);

    uninstall();
  });

  it("reads a zoom as a zoom, not as a keyboard", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();

    // iOS focus-zoom on a sub-16px field. 852 / 1.3 = 655.38 page pixels of
    // viewport, which the unscaled subtraction called a 197 px keyboard.
    viewport.zoomTo(1.3);

    expect(keyboard()).toBe("0px");

    // A real keyboard while zoomed is still a keyboard: 352 px of it, hidden.
    viewport.height = (852 - 352) / 1.3;
    viewport.dispatchEvent(new Event("resize"));
    expect(keyboard()).toBe("352px");

    uninstall();
  });

  it("treats a missing scale as 1", () => {
    const viewport = new FakeVisualViewport(500);
    // Safari before 13 and any stub that predates the property.
    (viewport as { scale?: number }).scale = undefined;
    install(viewport);
    const uninstall = installViewportVars();

    expect(keyboard()).toBe("352px");

    uninstall();
  });

  it("never reports a negative inset", () => {
    // iOS briefly reports a viewport taller than the window during rotation.
    install(new FakeVisualViewport(900));
    const uninstall = installViewportVars();

    expect(keyboard()).toBe("0px");
    expect(appHeight()).toBe("852px");

    uninstall();
  });
});

describe("useKeyboardOpen", () => {
  it("flips as the inset crosses the threshold, not at any inset at all", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);

    const { result } = renderHook(() => useKeyboardOpen());
    expect(result.current).toBe(false);

    act(() => viewport.resizeTo(852 - KEYBOARD_OPEN_PX)); // inset exactly at the line — not open
    expect(result.current).toBe(false);

    act(() => viewport.resizeTo(852 - KEYBOARD_OPEN_PX - 1)); // one past it — open
    expect(result.current).toBe(true);

    act(() => viewport.scrollTo(1)); // the scroll takes it back to the line
    expect(result.current).toBe(false);

    act(() => viewport.resizeTo(852));
    expect(result.current).toBe(false);
  });
});
