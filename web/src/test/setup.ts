import "@testing-library/jest-dom";

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
