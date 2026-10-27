import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  createRoot,
  failInstall,
  installAudioUnlock,
  installServiceWorker,
  installViewportVars,
  render,
} = vi.hoisted(() => {
  const render = vi.fn();
  // Pending until a test settles it: the test that wants a failed registration
  // rejects the promise its own run of main was handed.
  let rejectInstall: (error: unknown) => void = () => {};
  return {
    createRoot: vi.fn(() => ({ render })),
    failInstall: (error: unknown) => rejectInstall(error),
    installAudioUnlock: vi.fn(),
    installServiceWorker: vi.fn(
      () =>
        new Promise<() => void>((_resolve, reject) => {
          rejectInstall = reject;
        }),
    ),
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
vi.mock("@/lib/sw", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sw")>()),
  installServiceWorker,
}));
vi.mock("react-dom/client", () => ({ createRoot }));

// main.tsx runs once per module load, and vitest clears every mock's calls
// before each test (`clearMocks`, on by default since vitest 5). So each test
// loads it afresh, and sees only the calls of the run it caused.
beforeEach(() => {
  vi.resetModules();
});

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

  it("installs the service worker after handing the app to React, and reports a failure", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    await import("./main");

    // With no options: `import.meta.env.PROD` decides between registering and,
    // in dev, unregistering. Last, so nothing of the worker's stands between
    // the module and the app being handed to React.
    expect(installServiceWorker).toHaveBeenCalledExactlyOnceWith();
    expect(installServiceWorker.mock.invocationCallOrder[0]).toBeGreaterThan(
      render.mock.invocationCallOrder[0] ?? Infinity,
    );

    // What register() does with a /sw.js served as index.html. Dropped, it would
    // be an unhandled rejection; reported, it names the deploy that did not
    // ship the file.
    const refused = new DOMException(
      "The script has an unsupported MIME type ('text/html').",
      "SecurityError",
    );
    failInstall(refused);
    await vi.waitFor(() =>
      expect(error).toHaveBeenCalledWith("service worker setup failed", refused),
    );
  });
});
