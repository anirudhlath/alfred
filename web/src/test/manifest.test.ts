import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { applyTheme, type Theme } from "@/lib/theme";
import { srgbHex, token } from "./contrast";

/**
 * Every JSON file that is really in `web/public/`, keyed by the URL `mount_spa`
 * serves it at. A glob rather than two `?raw` imports, because the URLs
 * `applyTheme` hands the browser have to be checked against the directory and
 * not against a copy of themselves: renaming `manifest-light.json` and fixing
 * the one import that named it left the suite green while the installed app
 * asked for a file the SPA fallback answers with `index.html` as 200
 * `text/html` — a manifest that fails to parse, with a console warning nobody
 * reads. Missing here throws at import, so the whole file goes red at once.
 */
const PUBLIC = new Map(
  Object.entries(
    import.meta.glob("../../public/*.json", { eager: true, query: "?raw", import: "default" }),
  ).map(([path, source]) => [
    `/${path.split("/").pop()}`,
    JSON.parse(source as string) as Record<string, unknown>,
  ]),
);

function served(url: string): Record<string, unknown> {
  const parsed = PUBLIC.get(url);
  if (parsed === undefined) {
    throw new Error(
      `web/public/ serves no JSON at ${url} — it has ${[...PUBLIC.keys()].join(", ")}`,
    );
  }
  return parsed;
}

/**
 * The ground colour, taken from `index.css` through `contrast.ts`'s tokens
 * rather than restated. `contrast.test.ts` holds `TOKENS` to the stylesheet
 * verbatim, so retuning `--bg` the sanctioned way fails here until the manifests
 * are re-coloured too — `icons.test.ts:32` set the idiom one commit ago. The
 * literals are the private `THEME_COLOR` record in `lib/theme.ts`, which is
 * nothing but this pair spelled again for the `theme-color` meta; the last test
 * below is what keeps the two spellings from parting.
 */
const ground = (theme: Theme) => srgbHex(token(theme, "bg"));

const dark = served("/manifest.json");
const light = served("/manifest-light.json");

describe("the manifest", () => {
  it("is an installable standalone app with a stable identity", () => {
    // `id` is what a browser keys the install on. Without it the id defaults to
    // start_url, and any later change to start_url orphans every install.
    expect(dark).toMatchObject({
      id: "/",
      name: "Alfred",
      short_name: "Alfred",
      description: "Your personal butler",
      start_url: "/",
      scope: "/",
      display: "standalone",
      orientation: "portrait",
    });
  });

  it("carries those members and no others", () => {
    // toMatchObject above is blind to anything extra, and an extra member in a
    // manifest is not inert: `display_override`, `related_applications` or a
    // `launch_handler` would each change how the installed app opens.
    expect(Object.keys(dark).sort()).toEqual([
      "background_color",
      "description",
      "display",
      "icons",
      "id",
      "name",
      "orientation",
      "scope",
      "short_name",
      "start_url",
      "theme_color",
    ]);
  });

  it("ships the raster icons iOS and Chrome each need", () => {
    expect(dark.icons).toEqual([
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any maskable" },
    ]);
  });

  it("carries each theme's own ground, as index.css declares it", () => {
    expect(dark.theme_color).toBe(ground("dark"));
    expect(dark.background_color).toBe(ground("dark"));
    expect(light.theme_color).toBe(ground("light"));
    expect(light.background_color).toBe(ground("light"));
  });

  it("differs between the two files in nothing but those two colours", () => {
    // Two files is the price of a static manifest and an hourly theme. The
    // price is only worth paying if they cannot drift apart in anything else.
    // Deleted rather than rest-destructured into `_t`/`_b`: this repo's eslint
    // reports those as unused, and the two spellings say the same thing.
    const strip = (m: Record<string, unknown>) => {
      const rest = { ...m };
      delete rest.theme_color;
      delete rest.background_color;
      return rest;
    };
    expect(strip(light)).toEqual(strip(dark));
  });
});

describe("the URL applyTheme points the link at", () => {
  beforeEach(() => {
    document.head.innerHTML =
      '<meta name="theme-color" content="#25221F">' +
      '<link rel="manifest" href="/manifest.json">';
  });

  afterEach(() => {
    document.head.innerHTML = "";
    document.documentElement.removeAttribute("data-theme");
  });

  const themes: Theme[] = ["dark", "light"];

  it.each(themes)("in the %s theme names a file that exists and carries that ground", (theme) => {
    applyTheme(theme);
    const href = document.querySelector('link[rel="manifest"]')?.getAttribute("href") ?? "";
    expect(PUBLIC.has(href), `applyTheme("${theme}") points at ${href}, which is not in public/`).toBe(
      true,
    );
    expect(served(href).background_color).toBe(ground(theme));
    expect(served(href).theme_color).toBe(ground(theme));
    // The meta and the manifest are the same fact told to two readers, and
    // `lib/theme.ts` spells the colour a second time to tell it. This is the
    // only thing holding that second spelling to the stylesheet.
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute("content")).toBe(
      ground(theme),
    );
  });
});
