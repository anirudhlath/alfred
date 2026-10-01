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

/**
 * What a cache operation that has been programmed to fail rejects with. The
 * name matters: a phone that is full rejects a `put` with `QuotaExceededError`,
 * and an installed PWA on iOS is exactly the thing that meets it — the origin
 * is evicted whole rather than trimmed when it goes over budget.
 */
const storageFailure = (name = "UnknownError"): never => {
  throw new DOMException("A storage error occurred.", name);
};

/** Enough of `Cache` for this worker: open, match, put, keys, delete. */
class FakeCache {
  // Declared and assigned rather than a `readonly` constructor parameter:
  // tsconfig's `erasableSyntaxOnly` rejects parameter properties (TS1294), and
  // `tsc -b` is the only one of lint/vitest/build that says so.
  readonly entries: Map<string, Response>;
  readonly refuses: (url: string) => boolean;
  readonly refusesPut: (url: string) => boolean;
  readonly refusesMatch: (url: string) => boolean;
  /** Called with the absolute key of every `put`, before any of them can fail. */
  readonly attempted: (url: string) => void;
  /** The origin a relative cache key resolves against — the worker's own. */
  readonly origin: string;
  constructor(
    entries: Map<string, Response>,
    refuses: (url: string) => boolean,
    refusesPut: (url: string) => boolean,
    refusesMatch: (url: string) => boolean,
    attempted: (url: string) => void,
    origin: string,
  ) {
    this.entries = entries;
    this.refuses = refuses;
    this.refusesPut = refusesPut;
    this.refusesMatch = refusesMatch;
    this.attempted = attempted;
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
    const url = absolute(key, this.origin);
    // A read can fail too, not only a write — the disk, not the lookup. Modelled
    // because the alternative is a `.catch` on every match that no test can
    // reach, and a worker whose offline path rejects with a DOMException instead
    // of falling back is indistinguishable from one that does not.
    if (this.refusesMatch(url)) return Promise.reject(new DOMException("A storage error occurred.", "UnknownError"));
    return Promise.resolve(this.entries.get(url)?.clone());
  }
  put(key: string | { url: string }, response: Response): Promise<void> {
    // Recorded first, and that ordering is the point: `puts` is what a test
    // asserts a doomed write was never ATTEMPTED on, and a rejection recorded
    // nowhere is indistinguishable from a call that never happened.
    this.attempted(absolute(key, this.origin));
    // Real `Cache.put` rejects a Response whose body is already read or locked.
    // Without this the difference between `await shell.clone().text()` and
    // `await shell.text()` is invisible here and fatal in a browser: a worker
    // that can never install on any phone, recoverable only by the kill switch.
    if (response.bodyUsed || response.body?.locked) {
      return Promise.reject(new TypeError("Failed to execute 'put' on 'Cache': Response body is already used"));
    }
    // Real `Cache.put` rejects a 206 outright (the spec step is explicit about
    // it: a partial body is not a cache entry). `response.ok` is 200–299, so a
    // worker that stores on `ok` alone meets this, and it arrives as a TypeError
    // from a line that looks like it cannot fail.
    if (response.status === 206) {
      return Promise.reject(
        new TypeError("Failed to execute 'put' on 'Cache': Partial response (status code 206) is unsupported"),
      );
    }
    const url = absolute(key, this.origin);
    // Real `Cache.put` reads the body to the END before it writes anything, and
    // this does too, which has two consequences a worker must be tested
    // against. The Response handed over is spent afterwards — a handler that
    // puts the page's own response instead of a clone breaks the page. And a
    // put of a NETWORK response is a body read with every hazard of one: a body
    // whose headers arrived and whose bytes stall leaves the put pending for
    // ever, exactly as `await response.text()` would be. Filing the object away
    // unread, as this once did, made that put look instant — and four puts in
    // sw.js took a network body with no deadline, which a real Chromium, with no
    // DevTools attached to the worker, held an install open on until its own
    // limit on the event failed it, at 300 s.
    //
    // What is stored is the bytes and the head rebuilt into a new Response, as
    // a real cache stores a copy rather than the object; a null body stays null,
    // since a 204 or 205 cannot be constructed with one.
    const read = response.body ? response.arrayBuffer() : Promise.resolve(null);
    // Through `settled` once the body is in, for the reason deletes are: a real
    // put goes to disk, so a worker that does not await one has left it outside
    // `waitUntil` and the browser is free to stop the worker before it lands. A
    // microtask would make the missing `await` invisible; a macrotask does not.
    return read.then((bytes) =>
      settled(() => {
        if (this.refusesPut(url)) storageFailure("QuotaExceededError");
        this.entries.set(
          url,
          new Response(bytes, {
            status: response.status,
            statusText: response.statusText,
            headers: response.headers,
          }),
        );
      }),
    );
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
 * The init a worker passes `self.fetch` beside its input, or `undefined` for
 * the one call in the file that passes none. Modelled because it is load
 * bearing on the navigation path: `{ ...init, signal }` is a non-empty init,
 * and a non-empty init rebuilds the Request — which demotes `mode: "navigate"`
 * to `"same-origin"` and clears the reload- and history-navigation flags.
 */
export interface FetchInit {
  cache?: string;
  signal?: AbortSignal;
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
  /**
   * What the browser wants the bytes FOR — `"document"`, `"script"`, `"style"`,
   * `"font"`, `""`. Carried because the fixtures are only honest with it: the
   * real app's `/assets/*` requests are `mode: "cors"` (index.html marks both
   * tags `crossorigin`), not the `"no-cors"` a hand-written spec defaults to,
   * and a suite that never sends the real mode cannot see a branch that turns
   * on it.
   */
  destination: string;
  /** Real `Headers`, so `get("range")` is as case-insensitive as a browser's. */
  headers: Headers;
}

/** What a test says to describe one. */
export interface RequestSpec {
  /** A path, or a whole URL when the point of the test is the URL's shape. */
  path: string;
  mode?: string;
  /** Defaults to `"document"` for a navigation and `""` for everything else. */
  destination?: string;
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
  // A browser sets these together for a TOP-LEVEL navigation, so a fixture
  // cannot say one without the other and stay real. Not for a nested one: a
  // navigation inside an <iframe> is mode "navigate", destination "iframe", and
  // <object>/<embed> are the same shape again — which is why this is only a
  // default, and why a test can state the pair itself.
  destination = mode === "navigate" ? "document" : "",
  method = "GET",
  origin = ORIGIN,
  headers = {},
}: RequestSpec): FetchRequest {
  // `new URL(path, origin)` ignores the base for an absolute URL, which is what
  // lets a test pass `blob:…`, `data:…` or `//host/x` as `path` and get exactly
  // the URL a browser would have resolved.
  return {
    url: new URL(path, origin).href,
    method,
    mode,
    destination,
    headers: new Headers(headers),
  };
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
   * portal, a black-holed route. Two things can end the worker's wait on one: an
   * `AbortSignal` it passed, which also ends the request; or a deadline it
   * raced, which ends only the wait and leaves this promise pending for ever.
   * A worker that does neither waits for ever itself, and the test that asked
   * for this fails on vitest's timeout — which is the correct verdict.
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
  /**
   * Program a `Cache.put` to reject with `QuotaExceededError`, which is what a
   * phone that is full really answers. A pathname, or a whole URL. This is the
   * axis a service worker is least likely to be written for and most likely to
   * meet: a `put` is not a network call, it is a disk write on a device whose
   * storage the user does not manage, and a handler that treats a failed write
   * as a failed fetch answers an online phone out of its cache.
   */
  refusePut(key: string): void;
  /** Program a `Cache.match` to reject — the read half of the same axis. */
  refuseMatch(key: string): void;
  /**
   * Program `caches.open()` itself to reject — the whole CacheStorage
   * unavailable, which Safari does under storage pressure and in some private
   * modes. A worker that cannot open its cache should behave like no worker at
   * all; without this seam, every `caches.open` in the file is assumed
   * infallible. With no argument, every open rejects.
   */
  refuseOpen(name?: string): void;
  /**
   * Program a page's `navigate()` to reject although the worker controls it,
   * with a TypeError, and record no navigation for it. A path, as `clients`
   * names the page. A page that has closed rejects; so, in Chromium, does one
   * whose own reload is under way — even as the worker's navigation replaces
   * that reload ("Cannot navigate to URL", measured in task 11). Not a corner
   * case for a worker that navigates on activate: activation fires
   * `controllerchange` at every page it hands over, and `src/lib/sw.ts`
   * answers that by reloading.
   */
  refuseNavigate(path: string): void;
  /** Every pathname `self.fetch` was called with, in order. */
  fetched: string[];
  /**
   * What `self.fetch` was HANDED, in order — the `Request` a worker forwards,
   * or the string one that rebuilt the URL passed. Forwarding matters: a
   * Request carries the mode, the credentials and the redirect mode of the
   * original, and re-fetching `request.url` silently turns `redirect: "manual"`
   * into `"follow"`, which is the classic way a worker hands a page a redirected
   * response it must not have.
   */
  handed: (string | { url: string })[];
  /**
   * The init handed over with each of those, positionally — `undefined` where
   * the worker passed none. A navigation must be forwarded with NO init at all:
   * see `FetchInit`. Nothing else in this file can see that, because the input
   * object is identical either way.
   */
  inits: (FetchInit | undefined)[];
  /**
   * The key of every `Cache.put` the worker ATTEMPTED, in order, whether it
   * succeeded, was refused or was rejected by the Cache API itself. `keys()`
   * answers what landed; this answers what was tried, and the difference is
   * where a guard that declines a doomed write lives — a 206 put rejects with a
   * TypeError, so "not stored" is true of a worker that never tried and of one
   * that tried and swallowed the failure.
   */
  puts: string[];
  /** Of those, the ones asked for with `{ cache: "reload" }`. */
  reloaded: string[];
  /**
   * The delay, in ms, of every deadline the worker armed, in order. Armed on
   * the harness's clock (`now`), so they fire in the order they fall due
   * against everything else on it — a `trickle()` body's pieces included —
   * without a test ever waiting ten real seconds for one.
   */
  deadlines: number[];
  /**
   * The harness's clock: virtual ms since the worker loaded. It does not tick
   * with the wall clock. Whenever something is armed on it, it jumps straight
   * to whatever falls due first and fires that, one timer per macrotask, so
   * every microtask a fired timer sets off — a read resolving, a deadline
   * cleared, the next one armed — has run before the clock moves again.
   *
   * The ORDER is what is real, and it is the whole difference between a stall
   * and a slow link: a deadline armed at 0 for 10 000 ms fires after a body
   * piece due at 9 000 and before one due at 10 080. A clock that fired every
   * deadline on the next macrotask, as this one once did, could not tell a body
   * that keeps arriving from one that has stopped.
   */
  readonly now: number;
  /**
   * A 200 whose headers arrive at once and whose body arrives a piece at a
   * time on the harness's clock: each of `pieces` `every` ms after the one
   * before, starting when it is fetched. Then the end — or, with `stall`, never
   * anything again, which is a body that made progress and then stopped.
   *
   * On the harness rather than beside `stalled()`, because it needs this
   * instance's clock. `every` under a worker's deadline and `pieces × every`
   * over it is a slow link that is still working, which is the case a deadline
   * on the whole body cuts off.
   */
  trickle(type: string, pieces: readonly string[], timing: { every: number; stall?: boolean }): Response;
  /**
   * The pathname of every response whose body the worker let go of before its
   * end — every branch of it cancelled, which a browser answers by tearing the
   * request down. Recorded per fetch, so a pathname fetched twice and released
   * once is here once.
   *
   * A cancel reaches the network only when nobody else holds a branch of the
   * body, and that is the point of recording it: a worker reading a copy of a
   * response the PAGE is also reading can cancel its copy and must not release
   * the page's, while one reading its own fetch should release it rather than
   * leave a stalled connection open behind a decision it has already made.
   */
  released: string[];
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
   * here. A name the file does not declare is a `ReferenceError` at load, which
   * is the correct verdict for a test asking after something that is gone.
   *
   * A classic worker exports nothing, so the only other way to reach a policy
   * function inside one is through whatever branch consults it. That is enough
   * for every other function in `sw.js` and it is not enough for `isRefused`:
   * half of what that guard is for is the requests it must NOT refuse, and from
   * outside "refused" and "not handled" are the same `undefined`. Same species
   * of reach as `cacheName()` below — one name read out of the shipped source,
   * red the moment the file renames it.
   *
   * Only `EXPOSABLE` names are allowed through, because this splices an
   * identifier into the evaluated source and the argument above justifies
   * exactly one of them. Reaching for `precache` or `purge` this way would be
   * testing a worker through a door no browser has, when both already have
   * branches that drive them; the allowlist is where that judgement lives
   * rather than in a doc comment nothing enforces.
   */
  inner: Partial<Record<Exposable, (request: FetchRequest) => boolean>>;
  /**
   * Fires install as a browser does, moving this worker through the
   * registration's slots on the way: `installing` while the event runs, then
   * `waiting` in state "installed" once everything it extended with has
   * resolved — replacing, and making "redundant", any worker already waiting —
   * or, when that rejects, out of every slot in state "redundant", leaving
   * whatever was waiting before it where it was. Each call is a new worker, as
   * each install is in a browser.
   */
  install(): Promise<void>;
  /**
   * Fires activate for the worker that is waiting — or, when none is, for one
   * that has just been installed off-stage, since a test that only exercises
   * activate needs no install in front of it. Before the event it leaves
   * `waiting` for `active` in state "activating", and once everything it
   * extended with has settled it is "activated", whether that work resolved or
   * not: a browser does not undo an activation whose waitUntil rejected.
   */
  activate(): Promise<void>;
  /**
   * `undefined` means the worker never called respondWith — i.e. left it to the
   * network. Resolves only once everything the worker passed to the event's
   * `waitUntil()` has settled, so a test can assert what the background half of
   * a handler did; a rejection that escapes one is rethrown here rather than
   * swallowed, because a browser only logs it and this is where it can be seen —
   * unless the response itself rejected, in which case that is the rejection
   * reported: it is the answer the page got, and the one a test is asking about.
   */
  request(spec: RequestSpec): Promise<Response | undefined>;
  /**
   * The same dispatch, with the two halves kept apart: `responded` is the raw
   * promise the worker passed `respondWith`, settling the moment the page has
   * its answer, and `settled` resolves when everything passed to `waitUntil`
   * has finished (rethrowing the first rejection that escaped one, as `request`
   * does).
   *
   * `request` awaits both in order, which is what nearly every test wants and
   * is precisely blind to the property this exists for: that the page is
   * answered BEFORE the background work runs. `await waitUntil(refresh(…))`
   * written as `await refresh(…)` passes every assertion `request` can make.
   */
  dispatch(spec: RequestSpec): {
    responded: Promise<Response | undefined>;
    settled: Promise<void>;
  };
  message(data: unknown): Promise<void>;
  skipWaitingCalls: number;
  claimCalls: number;
  unregisterCalls: number;
  /**
   * Whether an `unregister()` has FINISHED, which is not when it was called: a
   * real one is a job the browser runs, and its promise settles once the
   * registration is gone. Until then the registration is still there. A
   * navigation can be matched to it — and in Chromium one matched to a worker
   * that is still activating waits for it — and a `register()` of its script
   * revives it rather than making a new one.
   */
  unregistered: boolean;
  /**
   * The absolute URL of every navigation a page was sent on by `navigate()`, in
   * order — one that started, so never one that rejected.
   */
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

/**
 * The file-local names a test may reach into a worker for, and the whole list.
 * Every one of them is a pure predicate over a request, which is what makes the
 * single signature on `inner` honest; a name that is not one does not belong
 * here, and neither does one that a dispatched event can already drive.
 */
export const EXPOSABLE = ["isRefused"] as const;
export type Exposable = (typeof EXPOSABLE)[number];

/** How a test asks for a worker that is not the default one. */
export interface LoadOptions {
  /** File-local names to hand back on `inner`; see that field and `EXPOSABLE`. */
  expose?: readonly Exposable[];
  /**
   * Where the worker is registered, `ORIGIN`'s root by default. The one thing
   * it changes that matters is the SCHEME: a worker is a secure context, which
   * means https everywhere except `http://localhost`, and `npm run preview`
   * and this box's own container port are both that. Without this the http arm
   * of any same-scheme guard is unexercisable and a worker that refused its own
   * origin on localhost would pass every test here.
   *
   * It moves the whole instance — routes, cache keys, `client.url`, and the
   * origin `request()` resolves a bare path against. A scoped worker sent a
   * spec with no `origin` must get a SAME-origin request, or the test passes
   * because the worker refused a cross-origin URL rather than for its own
   * reason. The module-level `fetchRequest` default stays `ORIGIN`; only the
   * instance knows better, so only the instance overrides it.
   */
  scope?: string;
  /**
   * Install over an OLDER worker that is already active — a deploy that changed
   * the worker's bytes, rather than a phone meeting the app for the first time.
   * What it changes is `self.registration.active`: null on a first install, the
   * older worker on an update, and in both cases this worker once it activates.
   * It is the only case decision 7 is about. A first
   * install has nothing to wait behind, so a `skipWaiting()` there changes
   * nothing, and a worker that skips waiting exactly when there is someone to
   * take over from passes every test that only ever installs first.
   */
  update?: boolean;
  /**
   * The pages open in the worker's scope — its window clients. By default one
   * page at the root that no worker controls: the page that registered this
   * worker on a first install, which is the one page every install has.
   */
  clients?: readonly ClientSpec[];
}

/**
 * An open page, as a test describes it. Which worker controls it is the whole
 * of what a worker can tell about a page, and it moves as a browser moves it:
 *
 * - `controlled`: the older worker — `update` — controls it. The page was
 *   loaded while the previous build's worker was active, and is served by it.
 *   The spec's Activate algorithm re-points every such page at the activating
 *   worker, and fires `controllerchange` at it, BEFORE it dispatches
 *   `activate`. So inside that event these pages are already this worker's:
 *   `matchAll()` lists them and `navigate()` works on them, with no claim().
 * - otherwise no worker controls it — the page that registered this worker,
 *   or one loaded past the workers. `matchAll()` lists it only with
 *   `includeUncontrolled`, or once `claim()` has taken it, and `navigate()`
 *   rejects for it until then.
 *
 * Not modelled: a page controlled by a worker of some other registration —
 * from this worker it is indistinguishable from an uncontrolled one, so it
 * would add a name and no behaviour; the document a navigation brings in,
 * which `navigated` records the URL of and nothing more; and a claim() made
 * after the worker's own unregister(), which in Chromium took nothing and
 * never settled.
 */
export interface ClientSpec {
  /** Where the page is: a path, resolved against the instance's origin. */
  path: string;
  /** Controlled by the older worker. Needs `update`: a first install has none. */
  controlled?: boolean;
}

/** An open page as the fake `Clients` tracks it: where it is, and who controls it. */
interface OpenPage {
  url: string;
  controller: "older" | "this" | null;
}

/**
 * The slice of a `ServiceWorker` the fake registration hands out, in each of
 * its three slots — `installing`, `waiting` and `active` — every one of them
 * null when empty, never undefined, as a real registration's are. A worker that
 * reads a slot the fake left out would read undefined, pass here, and do
 * something else on a phone: `if (self.registration.installing)
 * self.skipWaiting()` in install passed every handshake test that way, and in a
 * browser it takes over on every update. `state` moves as a browser moves it;
 * the object is the same one from slot to slot, as it is in a browser.
 */
interface FakeServiceWorker {
  scriptURL: string;
  state: "installing" | "installed" | "activating" | "activated" | "redundant";
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
  const { expose = [], scope: scopeUrl = `${ORIGIN}/`, update = false, clients = [{ path: "/" }] } = options;
  /** This instance's notion of "us" — `ORIGIN` unless a test moved the scope. */
  const origin = new URL(scopeUrl).origin;
  /**
   * The open pages and who controls each: the older worker, this one, or none.
   * See `ClientSpec` for how that moves.
   */
  const pages = clients.map(({ path, controlled = false }): OpenPage => {
    if (controlled && !update) {
      throw new Error(`${path} cannot be controlled by an older worker on a first install; pass update: true`);
    }
    return { url: new URL(path, origin).href, controller: controlled ? "older" : null };
  });
  /** Absolute page hrefs whose `navigate()` rejects although this worker controls them. */
  const navigateRefusals = new Set<string>();
  const store = new Map<string, Map<string, Response>>();
  /** Keyed by absolute href, not pathname: a cross-origin URL is a different route. */
  const routes = new Map<string, Response>();
  /** Absolute hrefs that accept the connection and answer nothing. */
  const hangs = new Set<string>();
  /** Cache names and absolute entry hrefs whose deletion rejects. */
  const refusals = new Set<string>();
  /** Absolute entry hrefs whose `put` rejects with QuotaExceededError. */
  const putRefusals = new Set<string>();
  /** Absolute entry hrefs whose `match` rejects. */
  const matchRefusals = new Set<string>();
  /** Cache names whose `open` rejects; `"*"` is every name. */
  const openRefusals = new Set<string>();
  /** A list per type, not one handler: a real scope runs every listener it was given. */
  const listeners = new Map<string, ((event: unknown) => void)[]>();
  const harness: Partial<Harness> & {
    fetched: string[];
    reloaded: string[];
    navigated: string[];
    deadlines: number[];
    handed: (string | { url: string })[];
    inits: (FetchInit | undefined)[];
    puts: string[];
    released: string[];
  } = {
    fetched: [],
    reloaded: [],
    navigated: [],
    deadlines: [],
    handed: [],
    inits: [],
    puts: [],
    released: [],
  };
  let deadlinesCleared = 0;

  /* The clock `Harness.now` describes. One real macrotask per timer fired, and
     the earliest due first — insertion order breaking a tie, so two armed for
     the same moment fire in the order they were armed, as a real event loop's
     do. */
  let now = 0;
  let nextTimer = 1;
  const timers = new Map<number, { due: number; fire: () => void }>();
  let ticking = false;
  const tick = () => {
    ticking = false;
    let next: [number, { due: number; fire: () => void }] | undefined;
    for (const entry of timers) if (!next || entry[1].due < next[1].due) next = entry;
    if (!next) return;
    timers.delete(next[0]);
    now = next[1].due;
    try {
      next[1].fire();
    } finally {
      if (timers.size) wake();
    }
  };
  const wake = () => {
    if (ticking) return;
    ticking = true;
    setTimeout(tick, 0);
  };
  const after = (ms: number, fire: () => void): number => {
    const id = nextTimer++;
    timers.set(id, { due: now + ms, fire });
    wake();
    return id;
  };

  /**
   * What `self.fetch` hands back: the route's response behind a body of its
   * own, standing for this one request's connection — chunk for chunk what the
   * route sends. Cancelling it, which a worker does by cancelling every branch
   * of the body it holds, is what a browser answers by tearing the request
   * down, and it is recorded in `released`. Observed here and not at the route,
   * because the route keeps a branch of its own so that it can answer again,
   * and a cancel never gets past a branch somebody still holds.
   *
   * An ABORT of the signal the worker passed reaches past the headers too: it
   * errors a body still arriving, and every branch of it with it, since every
   * branch is fed from this one. That is what a real fetch does — the Fetch
   * Standard's abort steps error the response's body, and in Chromium the
   * other branch's read rejected `AbortError` (task 7's fourth fix round) — and
   * it is the difference between the two ways a worker can give up on a body:
   * a cancel stops at a branch somebody else holds, an abort does not. Not
   * modelled: an abort once the last byte is in, which the Fetch Standard
   * would still let error a branch that has not read it yet. No worker here
   * can reach that — `timed` clears its deadline when the headers arrive — and
   * nothing has measured it.
   */
  const connection = (served: Response, pathname: string, signal?: AbortSignal): Response => {
    if (!served.body) return served;
    const reader = served.body.getReader();
    let source!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        source = controller;
      },
      async pull(controller) {
        const { done, value } = await reader.read();
        if (done) controller.close();
        else controller.enqueue(value);
      },
      cancel() {
        harness.released.push(pathname);
      },
    });
    // A no-op on a body that has already ended, as erroring a closed stream is.
    signal?.addEventListener("abort", () =>
      source.error(new DOMException("The user aborted a request.", "AbortError")),
    );
    return new Response(body, {
      status: served.status,
      statusText: served.statusText,
      headers: served.headers,
    });
  };
  const ownCache = cacheName(source);
  /** Named as a function so neither `cache` nor `keys()` has to reach for `this`. */
  const cacheOrThrow = (): string => {
    if (!ownCache) throw new Error("this worker declares no VERSION; name the cache explicitly");
    return ownCache;
  };
  let skipWaitingCalls = 0;
  let claimCalls = 0;
  let unregisterCalls = 0;
  let unregistered = false;

  /**
   * A `WindowClient` over one page, as `matchAll()` hands one out: a new
   * object every call, as a browser's are. `navigate()` is refused for a page
   * this worker does not control, as the spec's is, and for one
   * `refuseNavigate()` names; a navigation that starts is recorded at the
   * call, which is when a browser sets it going.
   */
  const windowClient = (page: OpenPage) => ({
    url: page.url,
    navigate: (url: string) => {
      const refused =
        page.controller !== "this"
          ? "this worker does not control the page"
          : navigateRefusals.has(page.url)
            ? "the page is closing or already reloading"
            : undefined;
      if (refused) return Promise.reject(new TypeError(`Failed to navigate ${page.url}: ${refused}`));
      harness.navigated.push(new URL(url, origin).href);
      return Promise.resolve();
    },
  });

  const scope = {
    addEventListener(type: string, fn: (event: unknown) => void) {
      listeners.set(type, [...(listeners.get(type) ?? []), fn]);
    },
    registration: {
      scope: scopeUrl,
      // Empty until `install()` below puts a worker in it.
      installing: null as FakeServiceWorker | null,
      waiting: null as FakeServiceWorker | null,
      // The worker the registration serves pages with while this one installs:
      // an older one on an update, nothing on a first install. `activate()`
      // below replaces it with this worker, as a browser does before it fires
      // the activate event.
      active: (update
        ? { scriptURL: `${origin}/sw.js`, state: "activated" }
        : null) as FakeServiceWorker | null,
      // Counted at the call, done a macrotask later: see `Harness.unregistered`.
      unregister: () => {
        unregisterCalls += 1;
        return settled(() => {
          unregistered = true;
          return true;
        });
      },
    },
    // A promise, as the real one returns: a worker that chains on it would
    // otherwise fail here with a TypeError it would never meet on a phone.
    skipWaiting: () => {
      skipWaitingCalls += 1;
      return Promise.resolve();
    },
    clients: {
      // Takes every open page in scope, the ones no worker controlled
      // included, and fires `controllerchange` at each in a browser.
      claim: () => {
        claimCalls += 1;
        for (const page of pages) page.controller = "this";
        return Promise.resolve();
      },
      // A real `matchAll()` sees only the clients this worker CONTROLS unless
      // asked for `includeUncontrolled`. Inside activate that is exactly the
      // pages the older worker controlled — activation re-pointed them, see
      // `activate` below — and never a page no worker controlled: the pair a
      // worker that reloads "the open pages" must tell apart, because the page
      // that registered it is one of the open pages, and reloading that one
      // registers it again.
      matchAll: (options?: { includeUncontrolled?: boolean }) =>
        Promise.resolve(
          pages.filter((page) => options?.includeUncontrolled || page.controller === "this").map(windowClient),
        ),
    },
    caches: {
      open: (name: string) => {
        if (openRefusals.has("*") || openRefusals.has(name)) {
          return settled(() => storageFailure());
        }
        if (!store.has(name)) store.set(name, new Map());
        return Promise.resolve(
          new FakeCache(
            store.get(name)!,
            (url: string) => refusals.has(url),
            (url: string) => putRefusals.has(url),
            (url: string) => matchRefusals.has(url),
            (url: string) =>
              void harness.puts.push(url.startsWith(origin) ? new URL(url).pathname : url),
            origin,
          ),
        );
      },
      keys: () => Promise.resolve([...store.keys()]),
      delete: (name: string) =>
        settled(() => (refusals.has(name) ? storageFailure() : store.delete(name))),
    },
    // Real, so the worker's own AbortController semantics are exercised rather
    // than a model of them.
    AbortController,
    // Recorded, and armed on the harness's clock rather than the wall's: a
    // routed response settles in microtasks, before the clock can move, so a
    // healthy fetch always clears its deadline first and only a hung one — or
    // a body that goes quiet for longer than the deadline — ever reaches it.
    // That keeps a ten-second deadline testable without a ten-second wait.
    setTimeout: (fn: () => void, ms: number) => {
      harness.deadlines.push(ms);
      return after(ms, fn);
    },
    clearTimeout: (id: number) => {
      deadlinesCleared += 1;
      timers.delete(id);
    },
    fetch: vi.fn((input: string | { url: string }, init?: FetchInit) => {
      const url = new URL(typeof input === "string" ? input : input.url, origin);
      harness.fetched.push(url.pathname);
      // The input itself, not only its URL: forwarding the Request is what
      // keeps the page's mode, credentials and redirect mode.
      harness.handed.push(input);
      // And the init beside it, because on a navigation the init is the whole
      // story: any init that is not empty rebuilds the Request, and the Fetch
      // Standard's constructor demotes a mode of "navigate" to "same-origin"
      // and clears the reload- and history-navigation flags when it does.
      // Recorded rather than inferred — `handed` cannot show it, since the
      // object handed over is the same one either way.
      harness.inits.push(init);
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
      return Promise.resolve(connection(response.clone(), url.pathname, init?.signal));
    }),
  };

  /* The tail is appended to the shipped bytes rather than woven into them, so
     the source that registered these listeners is still the source that ships.
     It runs last, after every declaration the file makes. */
  for (const name of expose) {
    // Not a type-level check alone: `expose` is data by the time it gets here,
    // and this splices it into evaluated source.
    if (!EXPOSABLE.includes(name)) throw new Error(`${name} is not an exposable worker internal`);
  }
  const tail = expose.length ? `\n;return { ${expose.join(", ")} };` : "";
  const inner = (new Function("self", ...SEALED, source + tail)(scope, ...SEALED.map(seal)) ??
    {}) as Harness["inner"];

  /** Runs every listener and waits on every promise each one extended with. */
  const extendable = async (type: string, data?: unknown) => {
    const waited: Promise<unknown>[] = [];
    for (const listener of listeners.get(type) ?? []) {
      listener({ data, waitUntil: (p: Promise<unknown>) => void waited.push(p) });
    }
    await Promise.all(waited);
  };

  /** See `Harness.dispatch`; shared so that `request` cannot drift from it. */
  const dispatch: Harness["dispatch"] = (spec) => {
    // The same builder the direct tests use, so a request dispatched through
    // an event and one handed to `isRefused` cannot drift apart — with this
    // instance's origin as the default, not the module's.
    const request = fetchRequest({ origin, ...spec });
    let answered: Promise<Response> | undefined;
    // A FetchEvent is an ExtendableEvent: `waitUntil` is what keeps the worker
    // alive for work that must outlive the response — the shell refresh is
    // exactly that, and without this seam it would have to be awaited before
    // the page is answered, which is a navigation blocked on a background
    // fetch.
    const extended: Promise<unknown>[] = [];
    for (const listener of listeners.get("fetch") ?? []) {
      listener({
        request,
        respondWith: (p: Promise<Response>) => {
          // Real `respondWith` throws InvalidStateError on a second call.
          if (answered) throw new Error("respondWith called twice for one fetch event");
          answered = p;
        },
        waitUntil: (p: Promise<unknown>) => void extended.push(p),
      });
    }
    let escaped: unknown;
    // Drained in a loop rather than awaited once, because a handler may call
    // `waitUntil` AFTER an await — the shell refresh is armed once the network
    // has answered, long after the listener returned — and a real event is
    // still extendable then, since `respondWith`'s own promise is holding it
    // open. Snapshotting the array here instead would silently wait on
    // nothing, and every assertion about background work would read the cache
    // before it was written.
    const drain = async () => {
      while (extended.length) {
        await Promise.all(extended.splice(0)).catch((error: unknown) => {
          escaped ??= error;
        });
      }
    };
    // `undefined` is the meaningful answer: the worker declined to handle it
    // and the browser goes to the network untouched.
    const responded = answered ? answered.then((r) => r as Response | undefined) : Promise.resolve(undefined);
    const settled = (async () => {
      // The response's own rejection is the caller's to see on `responded`;
      // swallowed here only so that awaiting `settled` is never the thing
      // that reports it, and so that a caller awaiting `responded` first is
      // not racing an unhandled rejection.
      try {
        await responded;
      } catch {
        /* reported through `responded` */
      }
      await drain();
      // A browser logs this and carries on serving. Here it is a defect: every
      // background path in sw.js is supposed to swallow its own failures, and a
      // rejection that reaches this line is one that does not.
      if (escaped) throw escaped;
    })();
    return { responded, settled };
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
    handed: harness.handed,
    inits: harness.inits,
    puts: harness.puts,
    get deadlinesCleared() {
      return deadlinesCleared;
    },
    get now() {
      return now;
    },
    released: harness.released,
    trickle: (type, pieces, { every, stall = false }) => {
      const encoder = new TextEncoder();
      let next = 0;
      return new Response(
        new ReadableStream<Uint8Array>(
          {
            // Pulled, not pushed: a piece is sent `every` ms after the reader
            // asked for more, the way a slow link delivers. Nothing is asked
            // for until the response is fetched, so the clock starts there.
            pull(controller) {
              if (next === pieces.length) {
                if (!stall) return controller.close();
                // Asked for more and never answering: a promise that never
                // settles means this is never pulled again, and nothing ends it.
                return new Promise<void>(() => {});
              }
              return new Promise<void>((resolve) => {
                after(every, () => {
                  controller.enqueue(encoder.encode(pieces[next++]));
                  resolve();
                });
              });
            },
          },
          { highWaterMark: 0 },
        ),
        { status: 200, headers: { "content-type": type } },
      );
    },
    respond: (pathname, response) => void routes.set(new URL(pathname, origin).href, response),
    hang: (pathname) => void hangs.add(new URL(pathname, origin).href),
    // A cache name has no slash and a URL always does, which is the whole of
    // the distinction: `caches.delete` is given the raw name, `Cache.delete` an
    // absolute href.
    refuseDelete: (key) =>
      void refusals.add(key.includes("/") ? new URL(key, origin).href : key),
    refusePut: (key) => void putRefusals.add(new URL(key, origin).href),
    refuseMatch: (key) => void matchRefusals.add(new URL(key, origin).href),
    refuseOpen: (name = "*") => void openRefusals.add(name),
    refuseNavigate: (path) => void navigateRefusals.add(new URL(path, origin).href),
    inner,
    install: async () => {
      const { registration } = scope;
      const installing: FakeServiceWorker = { scriptURL: `${origin}/sw.js`, state: "installing" };
      registration.installing = installing;
      try {
        await extendable("install");
      } catch (error) {
        registration.installing = null;
        installing.state = "redundant";
        throw error;
      }
      registration.installing = null;
      if (registration.waiting) registration.waiting.state = "redundant";
      registration.waiting = installing;
      installing.state = "installed";
    },
    activate: async () => {
      const { registration } = scope;
      const activating: FakeServiceWorker = registration.waiting ?? {
        scriptURL: `${origin}/sw.js`,
        state: "installed",
      };
      registration.waiting = null;
      registration.active = activating;
      activating.state = "activating";
      // Before the event, as the spec's Activate algorithm does it: every page
      // the older worker controlled is this worker's now.
      for (const page of pages) if (page.controller === "older") page.controller = "this";
      try {
        await extendable("activate");
      } finally {
        activating.state = "activated";
      }
    },
    dispatch,
    async request(spec) {
      const { responded, settled } = dispatch(spec);
      let response: Response | undefined;
      try {
        response = await responded;
      } catch (error) {
        // Awaited even when the response rejected, so that `waitUntil` work
        // started before the failure still finishes inside the test — but not
        // allowed to REPLACE the failure. A `finally` that awaited `settled`
        // did exactly that: a background rejection overwrote the response's
        // own, so a test pinning a navigation's error could be failed or
        // satisfied by unrelated background work. `dispatch().settled` still
        // reports the background half, for a test that wants it.
        await settled.catch(() => {});
        throw error;
      }
      await settled;
      return response;
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
    get unregistered() {
      return unregistered;
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

/**
 * A response whose headers arrive, then `sent`, and then nothing. `fetch()`
 * resolves on the headers, so this is what a route that stalls half way
 * through a body looks like from inside a worker: a 200 in hand, `response.ok`
 * true, the first read answered, and `await response.text()` pending for ever
 * on a request that is already "finished". The stream is never closed, which
 * is the state a connection that goes quiet mid-body leaves the reader in.
 *
 * Some bytes by default, and that is the half that matters. A body that never
 * sends one is caught by any deadline on its FIRST read, so a worker that read
 * one piece and called the body done passed every test written against the
 * silent version — and in Chromium with no DevTools attached to the worker,
 * against a chunk that sends some bytes and stalls, that worker's install stayed
 * `installing` until the browser's own limit on the event failed it, at 300 s.
 * `sent = ""` is the silent version, still worth a test of its own: it is the
 * one a deadline armed only once the first piece has arrived never ends.
 *
 * Its own hazard, and the reason this is a factory: the promise a reader is
 * left holding never settles, so anything that awaits one without a deadline
 * hangs the test rather than failing it.
 */
export const stalled = (type = "text/html; charset=utf-8", sent = "the first of it") =>
  new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        if (sent) controller.enqueue(new TextEncoder().encode(sent));
      },
    }),
    { status: 200, headers: { "content-type": type } },
  );

/**
 * A response whose headers arrive, then `sent`, and then an error: a connection
 * reset half way through a body. Unlike a stall it ENDS — the read after the
 * bytes rejects — and a stream that has errored rejects a cancel too, so a
 * worker that gives up on one by cancelling it has a second rejection to
 * handle, and one it leaves unhandled is logged by a browser and raised here.
 */
export const broken = (type = "text/html; charset=utf-8", sent = "the first of it") => {
  let pulls = 0;
  return new Response(
    new ReadableStream<Uint8Array>({
      pull(controller) {
        if (pulls++) controller.error(new TypeError("network error"));
        else controller.enqueue(new TextEncoder().encode(sent));
      },
    }),
    { status: 200, headers: { "content-type": type } },
  );
};
