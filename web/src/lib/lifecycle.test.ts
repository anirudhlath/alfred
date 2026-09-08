import { describe, expect, it, vi } from "vitest";
import { onVisible } from "./lifecycle";

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
  document.dispatchEvent(new Event("visibilitychange"));
}

function pageShow(persisted: boolean): void {
  const event = new Event("pageshow");
  Object.defineProperty(event, "persisted", { value: persisted });
  window.dispatchEvent(event);
}

describe("onVisible", () => {
  it("fires when the document becomes visible, not when it hides", () => {
    const fn = vi.fn();
    const off = onVisible(fn);

    setVisibility("hidden");
    expect(fn).not.toHaveBeenCalled();

    setVisibility("visible");
    expect(fn).toHaveBeenCalledTimes(1);

    off();
  });

  it("fires on a restore from the back-forward cache", () => {
    const fn = vi.fn();
    const off = onVisible(fn);

    pageShow(false);
    expect(fn).not.toHaveBeenCalled();

    pageShow(true);
    expect(fn).toHaveBeenCalledTimes(1);

    off();
  });

  it("stops once unsubscribed", () => {
    const fn = vi.fn();
    onVisible(fn)();

    setVisibility("visible");
    pageShow(true);

    expect(fn).not.toHaveBeenCalled();
  });
});
