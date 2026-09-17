import { vi } from "vitest";

/** Every URL in these tests is absolute, because a real `fetch` event's is. */
export const ORIGIN = "https://alfred.example.com";

const absolute = (key: string | { url: string }): string =>
  new URL(typeof key === "string" ? key : key.url, ORIGIN).href;

/** Enough of `Cache` for this worker: open, match, put, keys, delete. */
class FakeCache {
  // Declared and assigned rather than a `readonly` constructor parameter:
  // tsconfig's `erasableSyntaxOnly` rejects parameter properties (TS1294), and
  // `tsc -b` is the only one of lint/vitest/build that says so.
  readonly entries: Map<string, Response>;
  constructor(entries: Map<string, Response>) {
    this.entries = entries;
  }
  match(key: string | { url: string }): Promise<Response | undefined> {
    return Promise.resolve(this.entries.get(absolute(key)));
  }
  put(key: string | { url: string }, response: Response): Promise<void> {
    this.entries.set(absolute(key), response);
    return Promise.resolve();
  }
  /** Real `Cache.keys()` answers Requests carrying absolute URLs. So does this. */
  keys(): Promise<{ url: string }[]> {
    return Promise.resolve([...this.entries.keys()].map((url) => ({ url })));
  }
  delete(key: string | { url: string }): Promise<boolean> {
    return Promise.resolve(this.entries.delete(absolute(key)));
  }
}

export interface Harness {
  /** cache name → (absolute URL → Response). The whole of the fake CacheStorage. */
  store: Map<string, Map<string, Response>>;
  /** Program `self.fetch`: exact pathname → the response to answer with. */
  respond(pathname: string, response: Response): void;
  /** Every pathname `self.fetch` was called with, in order. */
  fetched: string[];
  /** Of those, the ones asked for with `{ cache: "reload" }`. */
  reloaded: string[];
  install(): Promise<void>;
  activate(): Promise<void>;
  /** `undefined` means the worker never called respondWith — i.e. left it to the network. */
  request(init: {
    path: string;
    mode?: string;
    method?: string;
    origin?: string;
  }): Promise<Response | undefined>;
  message(data: unknown): Promise<void>;
  skipWaitingCalls: number;
  claimCalls: number;
  unregisterCalls: number;
  navigated: string[];
  /** Everything currently in CACHE, as pathnames, sorted. */
  keys(name?: string): string[];
}

const CACHE = "alfred-shell-v1";

/**
 * Evaluate a service worker's shipped source against a fake global scope and
 * hand back the levers a test needs.
 *
 * `new Function("self", source)` is the whole trick: the worker reaches every
 * global through `self.`, so one object is the entire world it can see. Nothing
 * here is mocked at the module boundary — these tests run the bytes that ship.
 */
export function loadWorker(source: string): Harness {
  const store = new Map<string, Map<string, Response>>();
  const routes = new Map<string, Response>();
  const listeners = new Map<string, (event: unknown) => void>();
  const harness: Partial<Harness> & { fetched: string[]; reloaded: string[]; navigated: string[] } =
    { fetched: [], reloaded: [], navigated: [] };
  let skipWaitingCalls = 0;
  let claimCalls = 0;
  let unregisterCalls = 0;

  const scope = {
    addEventListener(type: string, fn: (event: unknown) => void) {
      listeners.set(type, fn);
    },
    registration: {
      scope: `${ORIGIN}/`,
      unregister: () => {
        unregisterCalls += 1;
        return Promise.resolve(true);
      },
    },
    skipWaiting: () => {
      skipWaitingCalls += 1;
    },
    clients: {
      claim: () => {
        claimCalls += 1;
        return Promise.resolve();
      },
      matchAll: () =>
        Promise.resolve([
          {
            url: `${ORIGIN}/`,
            navigate: (url: string) => {
              harness.navigated.push(url);
              return Promise.resolve();
            },
          },
        ]),
    },
    caches: {
      open: (name: string) => {
        if (!store.has(name)) store.set(name, new Map());
        return Promise.resolve(new FakeCache(store.get(name)!));
      },
      keys: () => Promise.resolve([...store.keys()]),
      delete: (name: string) => Promise.resolve(store.delete(name)),
    },
    fetch: vi.fn((input: string | { url: string }, init?: { cache?: string }) => {
      const path = new URL(typeof input === "string" ? input : input.url, ORIGIN).pathname;
      harness.fetched.push(path);
      // Recorded rather than asserted on the source: `cache: "reload"` is the
      // only thing between a worker installing seconds after a deploy and a
      // precache of the bytes that deploy just replaced.
      if (init?.cache === "reload") harness.reloaded.push(path);
      const response = routes.get(path);
      // No route programmed = no network. A real worker sees a TypeError here.
      if (!response) return Promise.reject(new TypeError(`no route for ${path}`));
      return Promise.resolve(response.clone());
    }),
  };

  new Function("self", source)(scope);

  const extendable = async (type: string) => {
    let waited: Promise<unknown> = Promise.resolve();
    listeners.get(type)?.({ waitUntil: (p: Promise<unknown>) => void (waited = p) });
    await waited;
  };

  return {
    store,
    fetched: harness.fetched,
    reloaded: harness.reloaded,
    navigated: harness.navigated,
    respond: (pathname, response) => void routes.set(pathname, response),
    install: () => extendable("install"),
    activate: () => extendable("activate"),
    async request({ path, mode = "no-cors", method = "GET", origin = ORIGIN }) {
      // A plain object, not a Request: undici refuses to construct one with
      // mode "navigate", and the worker reads only these three fields.
      const request = { url: new URL(path, origin).href, method, mode };
      let answered: Promise<Response> | undefined;
      listeners.get("fetch")?.({
        request,
        respondWith: (p: Promise<Response>) => void (answered = p),
      });
      // `undefined` is the meaningful answer: the worker declined to handle it
      // and the browser goes to the network untouched.
      return answered ? await answered : undefined;
    },
    async message(data) {
      let waited: Promise<unknown> = Promise.resolve();
      listeners.get("message")?.({ data, waitUntil: (p: Promise<unknown>) => void (waited = p) });
      await waited;
    },
    get skipWaitingCalls() {
      return skipWaitingCalls;
    },
    get claimCalls() {
      return claimCalls;
    },
    get unregisterCalls() {
      return unregisterCalls;
    },
    keys(name = CACHE) {
      return [...(store.get(name)?.keys() ?? [])].map((url) => new URL(url).pathname).sort();
    },
  };
}

const body = (type: string) => (text: string) =>
  new Response(text, { status: 200, headers: { "content-type": type } });

export const html = body("text/html; charset=utf-8");
export const js = body("text/javascript");
export const css = body("text/css");
export const font = () =>
  new Response("woff2", { status: 200, headers: { "content-type": "font/woff2" } });
