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

/** A field in the document with focus on it, as the keyboard would see it. */
function focusField(tag: "input" | "textarea" = "textarea", type?: string): HTMLElement {
  const field = document.createElement(tag);
  if (type && field instanceof HTMLInputElement) field.type = type;
  document.body.appendChild(field);
  field.focus();
  return field;
}

const appHeight = () => document.documentElement.style.getPropertyValue("--app-height");
const viewportTop = () => document.documentElement.style.getPropertyValue("--viewport-top");

beforeEach(() => setInnerHeight(852));

afterEach(() => {
  install(original as FakeVisualViewport | null);
  document.body.replaceChildren();
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
    focusField();
    const uninstall = installViewportVars();

    viewport.resizeTo(500);

    expect(appHeight()).toBe("500px");

    uninstall();
  });

  it("takes the visual viewport's word over the layout viewport's under a field", () => {
    // The frame that broke phase 2: iOS has shrunk the visual viewport for the
    // keyboard and the layout viewport has not caught up. The shell is the band
    // that is on screen — 500 px — and never the 852 px window around it.
    const viewport = new FakeVisualViewport(500);
    install(viewport);
    setInnerHeight(852);
    focusField();
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("500px");

    uninstall();
  });

  it("ignores a shrunken visual viewport when no field has focus", () => {
    // Measured on an iPhone with no keyboard up: the visual viewport read 547
    // against a layout viewport of 894, and went on reading it until a touch.
    const viewport = new FakeVisualViewport(894);
    install(viewport);
    setInnerHeight(894);
    const uninstall = installViewportVars();

    viewport.resizeTo(547);

    expect(appHeight()).toBe("894px");

    uninstall();
  });

  it("starts at the full height when it mounts into the shrunken reading", () => {
    install(new FakeVisualViewport(547));
    setInnerHeight(894);
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("894px");

    uninstall();
  });

  it("gives the height back the moment the field lets go", () => {
    const viewport = new FakeVisualViewport(500, 352);
    install(viewport);
    const field = focusField();
    const uninstall = installViewportVars();
    expect(appHeight()).toBe("500px");
    expect(viewportTop()).toBe("352px");

    // The Door taking focus away: the keyboard's resize has not arrived yet.
    field.blur();

    expect(appHeight()).toBe("852px");
    expect(viewportTop()).toBe("0px");

    uninstall();
  });

  it("does not follow a pan with no field focused", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.scrollTo(60);

    expect(viewportTop()).toBe("0px");

    uninstall();
  });

  it("reads the keyboard's band as soon as a field takes focus", () => {
    install(new FakeVisualViewport(500));
    const uninstall = installViewportVars();
    expect(appHeight()).toBe("852px");

    focusField();

    expect(appHeight()).toBe("500px");

    uninstall();
  });

  it("takes no keyboard from a control that raises none", () => {
    install(new FakeVisualViewport(547));
    focusField("input", "checkbox");
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("852px");

    uninstall();
  });

  it("counts a text input as a field", () => {
    install(new FakeVisualViewport(500));
    focusField("input", "password");
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("500px");

    uninstall();
  });

  it("follows the viewport when iOS pans it under a focused field", () => {
    const viewport = new FakeVisualViewport(500);
    install(viewport);
    focusField();
    const uninstall = installViewportVars();

    viewport.scrollTo(60);

    // The band did not change size, only where it starts.
    expect(appHeight()).toBe("500px");
    expect(viewportTop()).toBe("60px");

    uninstall();
  });

  it("rounds to whole pixels", () => {
    install(new FakeVisualViewport(500.4, 59.6));
    focusField();
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

    viewport.height = 500;
    focusField();
    expect(appHeight()).toBe("852px");

    viewport.scrollTo(60);
    expect(viewportTop()).toBe("0px");

    resizeWindowTo(400);
    expect(appHeight()).toBe("852px");
  });
});
