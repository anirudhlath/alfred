import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installViewportVars } from "./viewport";

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

function resizeWindowTo(value: number): void {
  setInnerHeight(value);
  window.dispatchEvent(new Event("resize"));
}

const appHeight = () => document.documentElement.style.getPropertyValue("--app-height");
const viewportTop = () => document.documentElement.style.getPropertyValue("--viewport-top");

beforeEach(() => setInnerHeight(852));

afterEach(() => {
  install(original as FakeVisualViewport | null);
  document.documentElement.style.removeProperty("--app-height");
  document.documentElement.style.removeProperty("--viewport-top");
});

describe("installViewportVars", () => {
  it("mirrors the visible band onto the document element", () => {
    install(new FakeVisualViewport(852));
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("852px");
    expect(viewportTop()).toBe("0px");

    uninstall();
  });

  it("shrinks the shell to the band the keyboard leaves", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.resizeTo(500);

    expect(appHeight()).toBe("500px");

    uninstall();
  });

  it("takes the visual viewport's word over the layout viewport's", () => {
    // The frame that broke phase 2: iOS has shrunk the visual viewport for the
    // keyboard and the layout viewport has not caught up. The shell is the band
    // that is on screen — 500 px — and never the 852 px window around it.
    const viewport = new FakeVisualViewport(500);
    install(viewport);
    setInnerHeight(852);
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("500px");

    uninstall();
  });

  it("follows the viewport when iOS pans it under a focused field", () => {
    const viewport = new FakeVisualViewport(500);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.scrollTo(60);

    // The band did not change size, only where it starts.
    expect(appHeight()).toBe("500px");
    expect(viewportTop()).toBe("60px");

    uninstall();
  });

  it("rounds to whole pixels", () => {
    install(new FakeVisualViewport(500.4, 59.6));
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("500px");
    expect(viewportTop()).toBe("60px");

    uninstall();
  });

  it("falls back to the window where there is no visual viewport", () => {
    install(null);
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("852px");
    expect(viewportTop()).toBe("0px");

    resizeWindowTo(400);
    expect(appHeight()).toBe("400px");

    uninstall();
  });

  it("follows window resizes with a visual viewport too", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.height = 300;
    resizeWindowTo(300); // rotation: the window event is the only one iOS sends

    expect(appHeight()).toBe("300px");

    uninstall();
  });

  it("stops updating once uninstalled", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();
    uninstall();

    viewport.resizeTo(500);
    expect(appHeight()).toBe("852px");

    viewport.scrollTo(60);
    expect(viewportTop()).toBe("0px");

    resizeWindowTo(400);
    expect(appHeight()).toBe("852px");
  });
});
