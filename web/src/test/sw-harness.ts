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
    // Real `Cache.put` rejects a Response whose body is already read or locked.
    // Without this the difference between `await shell.clone().text()` and
    // `await shell.text()` is invisible here and fatal in a browser: a worker
    // that can never install on any phone, recoverable only by the kill switch.
    if (response.bodyUsed || response.body?.locked) {
      return Promise.reject(new TypeError("Failed to execute 'put' on 'Cache': Response body is already used"));
    }
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
  /**
   * The cache the loaded worker names, read out of its own `VERSION` so that
   * bumping it moves these tests rather than making their assertions vacuous.
   * Throws for a worker that declares no version — `sw.kill.js` owns no cache.
   */
  cache: string;
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

/**
 * The worker's own cache name, built from the `VERSION` it declares. The
 * `alfred-` prefix is restated here rather than read, on purpose: it is the
 * invariant task 5's purge and task 11's kill switch both key on, so a worker
 * that stopped using it should turn these tests red.
 */
function cacheName(source: string): string | undefined {
  const version = /const VERSION = "([^"]+)"/.exec(source)?.[1];
  return version && `alfred-shell-${version}`;
}

/**
 * Globals a worker must not reach bare. Passed to `new Function` as parameters,
 * which shadows the real ones for the whole of the evaluated source — Node's
 * `fetch` is a live network call, and rule 3 of sw.js's header is what stops a
 * test silently exercising it. `eslint.config.js` refuses them at lint time;
 * this refuses them at run time, where the harness can say why.
 */
const SEALED = [
  "addEventListener",
  "caches",
  "clients",
  "fetch",
  "location",
  "registration",
  "skipWaiting",
] as const;

const seal = (name: string): unknown => {
  const complain = (): never => {
    throw new Error(`${name} was reached bare; every global in a worker goes through self.`);
  };
  return new Proxy(function sealed() {} as object, { get: complain, apply: complain });
};

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
  /** Keyed by absolute href, not pathname: a cross-origin URL is a different route. */
  const routes = new Map<string, Response>();
  /** A list per type, not one handler: a real scope runs every listener it was given. */
  const listeners = new Map<string, ((event: unknown) => void)[]>();
  const harness: Partial<Harness> & { fetched: string[]; reloaded: string[]; navigated: string[] } =
    { fetched: [], reloaded: [], navigated: [] };
  const ownCache = cacheName(source);
  /** Named as a function so neither `cache` nor `keys()` has to reach for `this`. */
  const cacheOrThrow = (): string => {
    if (!ownCache) throw new Error("this worker declares no VERSION; name the cache explicitly");
    return ownCache;
  };
  let skipWaitingCalls = 0;
  let claimCalls = 0;
  let unregisterCalls = 0;

  const client = {
    url: `${ORIGIN}/`,
    navigate: (url: string) => {
      harness.navigated.push(url);
      return Promise.resolve();
    },
  };

  const scope = {
    addEventListener(type: string, fn: (event: unknown) => void) {
      listeners.set(type, [...(listeners.get(type) ?? []), fn]);
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
      // A real `matchAll()` sees only the clients this worker CONTROLS, so an
      // activating worker gets an empty list until it has claimed them or asks
      // for `includeUncontrolled`. Modelled, because "then it reloads the open
      // pages" is a step that silently does nothing if the order is wrong.
      matchAll: (options?: { includeUncontrolled?: boolean }) =>
        Promise.resolve(options?.includeUncontrolled || claimCalls > 0 ? [client] : []),
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
      const url = new URL(typeof input === "string" ? input : input.url, ORIGIN);
      harness.fetched.push(url.pathname);
      // Recorded rather than asserted on the source: `cache: "reload"` is the
      // only thing between a worker installing seconds after a deploy and a
      // precache of the bytes that deploy just replaced.
      if (init?.cache === "reload") harness.reloaded.push(url.pathname);
      const response = routes.get(url.href);
      // No route programmed = no network. A real worker sees a TypeError here.
      if (!response) return Promise.reject(new TypeError(`no route for ${url.href}`));
      return Promise.resolve(response.clone());
    }),
  };

  new Function("self", ...SEALED, source)(scope, ...SEALED.map(seal));

  /** Runs every listener and waits on every promise each one extended with. */
  const extendable = async (type: string, data?: unknown) => {
    const waited: Promise<unknown>[] = [];
    for (const listener of listeners.get(type) ?? []) {
      listener({ data, waitUntil: (p: Promise<unknown>) => void waited.push(p) });
    }
    await Promise.all(waited);
  };

  return {
    store,
    get cache() {
      return cacheOrThrow();
    },
    fetched: harness.fetched,
    reloaded: harness.reloaded,
    navigated: harness.navigated,
    respond: (pathname, response) => void routes.set(new URL(pathname, ORIGIN).href, response),
    install: () => extendable("install"),
    activate: () => extendable("activate"),
    async request({ path, mode = "no-cors", method = "GET", origin = ORIGIN }) {
      // A plain object, not a Request: undici refuses to construct one with
      // mode "navigate", and the worker reads only these three fields.
      const request = { url: new URL(path, origin).href, method, mode };
      let answered: Promise<Response> | undefined;
      for (const listener of listeners.get("fetch") ?? []) {
        listener({
          request,
          respondWith: (p: Promise<Response>) => {
            // Real `respondWith` throws InvalidStateError on a second call.
            if (answered) throw new Error("respondWith called twice for one fetch event");
            answered = p;
          },
        });
      }
      // `undefined` is the meaningful answer: the worker declined to handle it
      // and the browser goes to the network untouched.
      return answered ? await answered : undefined;
    },
    message: (data) => extendable("message", data),
    get skipWaitingCalls() {
      return skipWaitingCalls;
    },
    get claimCalls() {
      return claimCalls;
    },
    get unregisterCalls() {
      return unregisterCalls;
    },
    keys(name = cacheOrThrow()) {
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
