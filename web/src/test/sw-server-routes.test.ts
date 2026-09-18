import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * The worker's refusal list against the server that actually serves these paths.
 *
 * `public/sw.js` has to name every route this origin answers with something that
 * is not the app, and it cannot discover them: it is a static file, and the
 * routes are FastAPI's. So the list is written by hand and pinned here — if the
 * server starts serving a non-app route the worker permits, this goes red rather
 * than a phone quietly caching Swagger UI as the app shell.
 *
 * In the Node project (`tsconfig.node.json`) rather than beside `sw.test.ts`,
 * for the reason `icons.test.ts` is: it reads files off disk, and `src`'s
 * browser project has `vite/client` types alone. Vite's `?raw` cannot reach
 * these either — they are outside `web/`, which is `server.fs`'s boundary.
 */
// `resolve(dirname(fileURLToPath(import.meta.url)), …)`, not
// `new URL(…, import.meta.url)`: the latter is Vite's asset syntax and the
// plugin rewrites it into something `node:fs` cannot read. Same reason
// `icons.test.ts` resolves its directory this way.
const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (path: string): string => readFileSync(resolve(REPO, path), "utf8");

const worker = read("web/public/sw.js");
const webServer = read("core/channels/web_server.py");
const spa = read("core/channels/spa.py");

/** The worker's own `NEVER` array, read out of the shipped bytes. */
const never: string[] = JSON.parse(/const NEVER = (\[[^\]]*\])/.exec(worker)![1]) as string[];
const neverUnder: string[] = JSON.parse(
  /const NEVER_UNDER = (\[[^\]]*\])/.exec(worker)![1],
) as string[];

/** `create_app`'s FastAPI(...) call, which is where a docs URL would be moved. */
const appCall = /app = FastAPI\(([^)]*)\)/.exec(webServer)![1];

describe("the worker's refusal list, against the routes the server really serves", () => {
  it.each([
    ["docs_url", "/docs"],
    ["redoc_url", "/redoc"],
    ["openapi_url", "/openapi.json"],
    ["swagger_ui_oauth2_redirect_url", "/docs/oauth2-redirect"],
  ])("still gets %s from FastAPI's default, which is %s", (kwarg, path) => {
    // The chain this rests on, and the link that can break silently: create_app
    // overrides none of these, so FastAPI's defaults are live, so those four
    // paths are non-app responses on this origin, so the worker must name them.
    // Move one — docs_url="/api-docs" — and the worker's list is wrong with no
    // other symptom until a navigation caches the wrong page as the shell.
    expect(appCall).not.toContain(kwarg);
    expect(never).toContain(path);
  });

  it("names every non-app route as an exact path, not as a prefix", () => {
    // Measured on the live app: /docsx, /docs/foo, /redocx and /openapi.json/x
    // are all 200 text/html — the app itself, via mount_spa's fallback. A /docs/
    // prefix would refuse them and cost a phone with no network the app.
    expect(neverUnder).toEqual(["/api/", "/ws/"]);
  });

  it("agrees with mount_spa about which prefixes are not the app", () => {
    // spa.py is the other half: these are the paths it 404s rather than
    // answering with index.html, and the worker refuses all of them. The two
    // lists are not identical and are not meant to be — spa.py knows nothing of
    // FastAPI's docs routes, which is exactly why the worker cannot be derived
    // from it — but nothing spa.py calls non-app may be missing from the worker.
    expect(spa).toContain('_NON_SPA_PREFIXES = ("api/", "ws")');
    expect(spa).toContain('_NON_SPA_PATHS = frozenset({"health"})');
    expect(never).toContain("/health");
    expect(never).toContain("/ws");
    expect(neverUnder).toContain("/api/");
    expect(neverUnder).toContain("/ws/");
  });

  it("refuses nothing the server answers with the app itself", () => {
    // The other direction, and the one a widening breaks. Every path here is a
    // measured 200 text/html from :8081.
    for (const path of ["/", "/workshop", "/apifoo", "/healthz", "/docsx", "/docs/foo"]) {
      expect(never).not.toContain(path);
      expect(neverUnder.some((prefix) => path.startsWith(prefix))).toBe(false);
    }
  });
});
