import { describe, expect, it } from "vitest";
import { THEME_COLOR } from "@/lib/theme";
// The shipped bytes, not a re-description of them. `?raw` and not `node:fs`:
// this file also imports `@/lib/theme`, which touches `document`, so it belongs
// to the browser TS project — where there is no `node` in `types` and no `@/`
// alias in the Node one. `index-html.test.ts` reads its subject the same way.
import darkSource from "../../public/manifest.json?raw";
import lightSource from "../../public/manifest-light.json?raw";

const dark = JSON.parse(darkSource) as Record<string, unknown>;
const light = JSON.parse(lightSource) as Record<string, unknown>;

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

  it("carries each theme's own ground", () => {
    expect(dark.theme_color).toBe(THEME_COLOR.dark);
    expect(dark.background_color).toBe(THEME_COLOR.dark);
    expect(light.theme_color).toBe(THEME_COLOR.light);
    expect(light.background_color).toBe(THEME_COLOR.light);
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
