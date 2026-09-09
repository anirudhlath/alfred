import { describe, expect, it } from "vitest";

describe("test environment", () => {
  it("answers matchMedia with a non-matching MediaQueryList", () => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    expect(mq.matches).toBe(false);
    expect(mq.media).toBe("(prefers-reduced-motion: reduce)");
    expect(() => mq.addEventListener("change", () => {})).not.toThrow();
  });

  it("provides a constructible ResizeObserver", () => {
    const observer = new ResizeObserver(() => {});
    expect(() => observer.observe(document.body)).not.toThrow();
    observer.disconnect();
  });

  it("provides a visualViewport that mirrors the window and takes listeners", () => {
    const viewport = window.visualViewport;
    expect(viewport).toBeTruthy();
    expect(viewport!.height).toBe(window.innerHeight);
    expect(viewport!.offsetTop).toBe(0);

    const seen: string[] = [];
    const onResize = () => seen.push("resize");
    viewport!.addEventListener("resize", onResize);
    viewport!.dispatchEvent(new Event("resize"));
    expect(seen).toEqual(["resize"]);
    viewport!.removeEventListener("resize", onResize);
  });

  it("registers the jest-dom matchers", () => {
    document.body.innerHTML = '<button type="button" disabled>x</button>';
    expect(document.querySelector("button")).toBeDisabled();
  });

  it("keeps a working localStorage on every supported Node", () => {
    localStorage.setItem("alfred.probe", "1");
    expect(localStorage.getItem("alfred.probe")).toBe("1");
    localStorage.removeItem("alfred.probe");
  });

  it("constructs pointer events with coordinates", () => {
    const event = new PointerEvent("pointerdown", { pointerId: 7, clientX: 42 });
    expect(event.pointerId).toBe(7);
    expect(event.clientX).toBe(42);
  });

  it("lets an element capture the pointer without throwing", () => {
    const el = document.createElement("div");
    expect(() => el.setPointerCapture(1)).not.toThrow();
    expect(() => el.releasePointerCapture(1)).not.toThrow();
  });
});
