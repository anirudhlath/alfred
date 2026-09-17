import { vi } from "vitest";

/** Every URL in these tests is absolute, because a real `fetch` event's is. */
export const ORIGIN = "https://alfred.example.com";

const absolute = (key: string | { url: string }, base: string): string =>
  new URL(typeof key === "string" ? key : key.url, base).href;

/**
 * A delete that is not done when it is called. Real `caches.delete()` and
 * `Cache.delete()` go to disk, so a worker that does not `await` one has left
 * it outside `waitUntil` — the event finishes, the browser is free to stop the
 * worker, and the cache the purge meant to drop is still there on the next
 * boot. Resolving in a microtask would hide that: microtasks drain before an
 * `await`ed event settles, so the missing `await` would look identical to the
 * `await`. A macrotask does not, which is the whole reason for this.
 */
const settled = <T>(value: () => T): Promise<T> =>
  new Promise((resolve, reject) =>
    setTimeout(() => {
      // A throw inside the timer callback would be an uncaught exception rather
      // than a rejected promise, which is not what a storage failure looks like.
      try {
        resolve(value());
      } catch (error) {
        reject(error);
      }
    }, 0),
  );

/** What a delete that has been programmed to fail rejects with. */
const storageFailure = (): never => {
  throw new DOMException("A storage error occurred.", "UnknownError");
};

/** Enough of `Cache` for this worker: open, match, put, keys, delete. */
class FakeCache {
  // Declared and assigned rather than a `readonly` constructor parameter:
  // tsconfig's `erasableSyntaxOnly` rejects parameter properties (TS1294), and
  // `tsc -b` is the only one of lint/vitest/build that says so.
  readonly entries: Map<string, Response>;
  readonly refuses: (url: string) => boolean;
  /** The origin a relative cache key resolves against — the worker's own. */
  readonly origin: string;
  constructor(entries: Map<string, Response>, refuses: (url: string) => boolean, origin: string) {
    this.entries = entries;
    this.refuses = refuses;
    this.origin = origin;
  }
  /**
   * Real `Cache.match` constructs a NEW Response every call, so reading one
   * body never spends the cache's copy. Handing back the stored object would
   * make `purge()`'s `await listed.json()` consume the shell manifest: the
   * record the next activate prunes against, gone after one read, and every
   * assertion about it after an activate reading a corpse.
   */
  match(key: string | { url: string }): Promise<Response | undefined> {
    return Promise.resolve(this.entries.get(absolute(key, this.origin))?.clone());
  }
  put(key: string | { url: string }, response: Response): Promise<void> {
    // Real `Cache.put` rejects a Response whose body is already read or locked.
    // Without this the difference between `await shell.clone().text()` and
    // `await shell.text()` is invisible here and fatal in a browser: a worker
    // that can never install on any phone, recoverable only by the kill switch.
    if (response.bodyUsed || response.body?.locked) {
      return Promise.reject(new TypeError("Failed to execute 'put' on 'Cache': Response body is already used"));
    }
    this.entries.set(absolute(key, this.origin), response);
    return Promise.resolve();
  }
  /** Real `Cache.keys()` answers Requests carrying absolute URLs. So does this. */
  keys(): Promise<{ url: string }[]> {
    return Promise.resolve([...this.entries.keys()].map((url) => ({ url })));
  }
  delete(key: string | { url: string }): Promise<boolean> {
    const url = absolute(key, this.origin);
    return settled(() => (this.refuses(url) ? storageFailure() : this.entries.delete(url)));
  }
}

/**
 * The fields of a fetch event's request the workers read. A plain object and
 * not a `Request`: undici refuses to construct one with mode `"navigate"`, and
 * that is the mode the whole navigation branch turns on.
 */
export interface FetchRequest {
  url: string;
  method: string;
  mode: string;
  /** Real `Headers`, so `get("range")` is as case-insensitive as a browser's. */
  headers: Headers;
}

/** What a test says to describe one. */
export interface RequestSpec {
  /** A path, or a whole URL when the point of the test is the URL's shape. */
  path: string;
  mode?: string;
  method?: string;
  origin?: string;
  headers?: Record<string, string>;
}

/**
 * Build the request a fetch event would carry. Exported because the refusal is
 * tested both ways — through a dispatched event, and called directly with one
 * of these.
 */
export function fetchRequest({
  path,
  mode = "no-cors",
  method = "GET",
  origin = ORIGIN,
  headers = {},
}: RequestSpec): FetchRequest {
  // `new URL(path, origin)` ignores the base for an absolute URL, which is what
  // lets a test pass `blob:…`, `data:…` or `//host/x` as `path` and get exactly
  // the URL a browser would have resolved.
  return { url: new URL(path, origin).href, method, mode, headers: new Headers(headers) };
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
  /**
   * Program `self.fetch` to accept the connection and never answer — a captive
   * portal, a black-holed route. The only thing that can end it is the worker's
   * own `AbortSignal`; a worker without one waits for ever, and the test that
   * asked for this fails on vitest's timeout, which is the correct verdict.
   */
  hang(pathname: string): void;
  /**
   * Program a delete to reject, which is the one failure a real `CacheStorage`
   * produces that this fake otherwise cannot — and therefore the only way a
   * test can reach a worker's per-delete `catch`. A cache NAME for
   * `caches.delete`, a pathname for `Cache.delete`; they are told apart by the
   * slash, which every URL has and no cache name here does.
   */
  refuseDelete(key: string): void;
  /** Every pathname `self.fetch` was called with, in order. */
  fetched: string[];
  /** Of those, the ones asked for with `{ cache: "reload" }`. */
  reloaded: string[];
  /**
   * The delay, in ms, of every deadline the worker armed, in order. Fired at
   * 0 ms here regardless — a routed response resolves in a microtask and clears
   * its timer long before the macrotask runs, so only a hung fetch ever sees
   * one — but the requested value is recorded so a test can pin it.
   */
  deadlines: number[];
  /**
   * How many of those the worker cleared. A deadline left armed still fires
   * ten seconds later, against a request that finished long ago: harmless in
   * itself, but it keeps the worker alive to do nothing, and a phone is where
   * that is least welcome. Equal to `deadlines.length` for a tidy worker.
   */
  deadlinesCleared: number;
  /**
   * The worker's own file-local functions, by the name the file declares them
   * under — `loadWorker(source, { expose: ["isRefused"] })` is what puts one
   * here, and a
   * name the file does not declare is a `ReferenceError` at load, which is the
   * correct verdict for a test asking after something that is gone.
   *
   * A classic worker exports nothing, so the only other way to reach a policy
   * function inside one is through whatever branch consults it. That is enough
   * for every other function in `sw.js` and it is not enough for `isRefused`:
   * half of what that guard is for is the requests it must NOT refuse, and from
   * outside "refused" and "not handled" are the same `undefined`. Same species
   * of reach as `cacheName()` below — one name read out of the shipped source,
   * red the moment the file renames it.
   */
  inner: Record<string, (...args: never[]) => unknown>;
  install(): Promise<void>;
  activate(): Promise<void>;
  /** `undefined` means the worker never called respondWith — i.e. left it to the network. */
  request(spec: RequestSpec): Promise<Response | undefined>;
  message(data: unknown): Promise<void>;
  skipWaitingCalls: number;
  claimCalls: number;
  unregisterCalls: number;
  navigated: string[];
  /** Everything currently in CACHE, as pathnames, sorted. */
  keys(name?: string): string[];
}

/**
 * The worker's own cache name, built from the `VERSION` it declares, so that a
 * bump moves these tests rather than making them vacuous. The `alfred-` prefix
 * is restated here rather than read because there is nothing to read it from —
 * the worker builds the name from a template literal and exports nothing.
 *
 * It is NOT an invariant anything keys on: `purge()` deletes every cache that
 * is not the current one, prefix or no prefix, and `sw.kill.js` deletes every
 * cache unfiltered. What this catches is a worker that renames its cache — the
 * tests go red because the name moved, not because a prefix rule was broken.
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
  "AbortController",
  "addEventListener",
  "caches",
  "clearTimeout",
  "clients",
  "fetch",
  "location",
  "registration",
  "setTimeout",
  "skipWaiting",
] as const;

const seal = (name: string): unknown => {
  const complain = (): never => {
    throw new Error(`${name} was reached bare; every global in a worker goes through self.`);
  };
  return new Proxy(function sealed() {} as object, { get: complain, apply: complain });
};

/** How a test asks for a worker that is not the default one. */
export interface LoadOptions {
  /** File-local names to hand back on `inner`; see that field. */
  expose?: readonly string[];
  /**
   * Where the worker is registered, `ORIGIN`'s root by default. The one thing
   * it changes that matters is the SCHEME: a worker is a secure context, which
   * means https everywhere except `http://localhost`, and `npm run preview`
   * and this box's own container port are both that. Without this the http arm
   * of any same-scheme guard is unexercisable and a worker that refused its own
   * origin on localhost would pass every test here.
   *
   * It moves the whole instance — routes, cache keys, `client.url` — so a test
   * using it passes `origin` to `fetchRequest` too, since that default is
   * module-level and stays `ORIGIN`.
   */
  scope?: string;
}

/**
 * Evaluate a service worker's shipped source against a fake global scope and
 * hand back the levers a test needs.
 *
 * `new Function("self", source)` is the whole trick: the worker reaches every
 * global through `self.`, so one object is the entire world it can see. Nothing
 * here is mocked at the module boundary — these tests run the bytes that ship.
 *
 */
export function loadWorker(source: string, options: LoadOptions = {}): Harness {
  const { expose = [], scope: scopeUrl = `${ORIGIN}/` } = options;
  /** This instance's notion of "us" — `ORIGIN` unless a test moved the scope. */
  const origin = new URL(scopeUrl).origin;
  const store = new Map<string, Map<string, Response>>();
  /** Keyed by absolute href, not pathname: a cross-origin URL is a different route. */
  const routes = new Map<string, Response>();
  /** Absolute hrefs that accept the connection and answer nothing. */
  const hangs = new Set<string>();
  /** Cache names and absolute entry hrefs whose deletion rejects. */
  const refusals = new Set<string>();
  /** A list per type, not one handler: a real scope runs every listener it was given. */
  const listeners = new Map<string, ((event: unknown) => void)[]>();
  const harness: Partial<Harness> & {
    fetched: string[];
    reloaded: string[];
    navigated: string[];
    deadlines: number[];
  } = { fetched: [], reloaded: [], navigated: [], deadlines: [] };
  let deadlinesCleared = 0;
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
    url: `${origin}/`,
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
      scope: scopeUrl,
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
        return Promise.resolve(
          new FakeCache(store.get(name)!, (url: string) => refusals.has(url), origin),
        );
      },
      keys: () => Promise.resolve([...store.keys()]),
      delete: (name: string) =>
        settled(() => (refusals.has(name) ? storageFailure() : store.delete(name))),
    },
    // Real, so the worker's own AbortController semantics are exercised rather
    // than a model of them.
    AbortController,
    // The requested delay is recorded and the timer is armed at 0 ms: a routed
    // response settles in a microtask, which runs before any macrotask, so a
    // healthy fetch always clears its deadline first and only a hung one ever
    // reaches the abort. That keeps the deadline testable without a real wait.
    setTimeout: (fn: () => void, ms: number) => {
      harness.deadlines.push(ms);
      return setTimeout(fn, 0);
    },
    clearTimeout: (id: ReturnType<typeof setTimeout>) => {
      deadlinesCleared += 1;
      clearTimeout(id);
    },
    fetch: vi.fn(
      (input: string | { url: string }, init?: { cache?: string; signal?: AbortSignal }) => {
        const url = new URL(typeof input === "string" ? input : input.url, origin);
        harness.fetched.push(url.pathname);
        // Recorded rather than asserted on the source: `cache: "reload"` is the
        // only thing between a worker installing seconds after a deploy and a
        // precache of the bytes that deploy just replaced.
        if (init?.cache === "reload") harness.reloaded.push(url.pathname);
        if (hangs.has(url.href)) {
          // Answers nothing, ever. A worker that passed no signal is left
          // hanging on purpose — that IS the bug being tested for.
          return new Promise<Response>((_, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("The user aborted a request.", "AbortError")),
            );
          });
        }
        const response = routes.get(url.href);
        // No route programmed = no network. A real worker sees a TypeError here.
        if (!response) return Promise.reject(new TypeError(`no route for ${url.href}`));
        return Promise.resolve(response.clone());
      },
    ),
  };

  /* The tail is appended to the shipped bytes rather than woven into them, so
     the source that registered these listeners is still the source that ships.
     It runs last, after every declaration the file makes. */
  const tail = expose.length ? `\n;return { ${expose.join(", ")} };` : "";
  const inner = (new Function("self", ...SEALED, source + tail)(scope, ...SEALED.map(seal)) ??
    {}) as Record<string, (...args: never[]) => unknown>;

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
    deadlines: harness.deadlines,
    get deadlinesCleared() {
      return deadlinesCleared;
    },
    respond: (pathname, response) => void routes.set(new URL(pathname, origin).href, response),
    hang: (pathname) => void hangs.add(new URL(pathname, origin).href),
    // A cache name has no slash and a URL always does, which is the whole of
    // the distinction: `caches.delete` is given the raw name, `Cache.delete` an
    // absolute href.
    refuseDelete: (key) =>
      void refusals.add(key.includes("/") ? new URL(key, origin).href : key),
    inner,
    install: () => extendable("install"),
    activate: () => extendable("activate"),
    async request(spec) {
      // The same builder the direct tests use, so a request dispatched through
      // an event and one handed to `isRefused` cannot drift apart.
      const request = fetchRequest(spec);
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
