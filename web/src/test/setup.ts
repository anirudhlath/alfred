import "@testing-library/jest-dom/vitest";

// Node 22 leaves localStorage to jsdom. Node >=26 defines its own `localStorage`
// global that stays `undefined` unless the process is started with
// --localstorage-file, and because vitest's jsdom environment shares one object
// with globalThis, that undefined own-property wins over jsdom's implementation.
// Modules reading localStorage at construction time then throw
// "Cannot read properties of undefined (reading 'getItem')".
//
// Install a minimal in-memory Storage when the global is missing so the suite
// behaves the same on both Node versions. No-op where jsdom's already works.
if (typeof globalThis.localStorage === "undefined") {
  const store = new Map<string, string>();
  const memoryStorage: Storage = {
    get length() {
      return store.size;
    },
    key: (index) => [...store.keys()][index] ?? null,
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => void store.set(key, String(value)),
    removeItem: (key) => void store.delete(key),
    clear: () => store.clear(),
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: memoryStorage,
    configurable: true,
    writable: true,
  });
}

// jsdom implements none of matchMedia, ResizeObserver or visualViewport, and all
// three are load-bearing: the reduced-motion branch in Layer, the Timeline's
// resize anchor, and installViewportVars. Stub them here so no test has to; a
// test that needs a different answer overrides its own with vi.stubGlobal.
if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  });
}

if (typeof globalThis.ResizeObserver === "undefined") {
  class TestResizeObserver implements ResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    writable: true,
    value: TestResizeObserver,
  });
}

// A real EventTarget, so installViewportVars() can add listeners and a test can
// dispatch `resize`/`scroll` at it. Defaults mirror the jsdom window, which means
// keyboardInset() is 0 until a test says otherwise.
class TestVisualViewport extends EventTarget {
  width = window.innerWidth;
  height = window.innerHeight;
  offsetTop = 0;
  offsetLeft = 0;
  pageTop = 0;
  pageLeft = 0;
  scale = 1;
}

if (!window.visualViewport) {
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    writable: true,
    value: new TestVisualViewport() as unknown as VisualViewport,
  });
}

// jsdom implements neither PointerEvent nor pointer capture, and both are
// load-bearing: hold-to-talk and slide-to-confirm are pointer-driven, and both
// capture the pointer so a finger that drifts off the control still reports up.
if (typeof globalThis.PointerEvent !== "function") {
  class TestPointerEvent extends MouseEvent {
    pointerId: number;
    pointerType: string;
    isPrimary: boolean;

    constructor(type: string, params: PointerEventInit = {}) {
      super(type, params);
      this.pointerId = params.pointerId ?? 1;
      this.pointerType = params.pointerType ?? "touch";
      this.isPrimary = params.isPrimary ?? true;
    }
  }
  Object.defineProperty(globalThis, "PointerEvent", {
    configurable: true,
    writable: true,
    value: TestPointerEvent,
  });
}

if (typeof Element.prototype.setPointerCapture !== "function") {
  Element.prototype.setPointerCapture = function setPointerCapture() {};
  Element.prototype.releasePointerCapture = function releasePointerCapture() {};
  Element.prototype.hasPointerCapture = function hasPointerCapture() {
    return false;
  };
}
