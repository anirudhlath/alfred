import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

const { createRoot, installAudioUnlock, installViewportVars, render } = vi.hoisted(() => {
  const render = vi.fn();
  return {
    createRoot: vi.fn(() => ({ render })),
    installAudioUnlock: vi.fn(),
    installViewportVars: vi.fn(),
    render,
  };
});

vi.mock("@/lib/audio", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/audio")>()),
  installAudioUnlock,
}));
vi.mock("@/lib/viewport", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/viewport")>()),
  installViewportVars,
}));
vi.mock("react-dom/client", () => ({ createRoot }));

describe("main", () => {
  it("installs the viewport and audio hooks, then renders the App under StrictMode", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    await import("./main");

    expect(installViewportVars).toHaveBeenCalledOnce();
    expect(installAudioUnlock).toHaveBeenCalledOnce();
    expect(createRoot).toHaveBeenCalledWith(document.getElementById("root"));
    expect(render).toHaveBeenCalledOnce();
    expect(render.mock.calls[0]?.[0]).toMatchObject({ type: StrictMode });

    // Both before the first paint: the root is sized from the viewport vars, and
    // a tap that lands before the unlock listener is installed unlocks nothing.
    const order = [installViewportVars, installAudioUnlock, render].map(
      (fn) => fn.mock.invocationCallOrder[0],
    );
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });
});
