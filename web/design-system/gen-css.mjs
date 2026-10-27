// Derives the design system's stylesheet from web/src/index.css, the app's one
// source of truth for tokens, type, motion and component classes. Only three
// edits, each because a design canvas is not the installed app:
//   1. The tokens answer to any `[data-theme]` element, not only `:root`, so a
//      `<Page theme="light">` beside a dark one carries its own palette.
//   2. The app shell's base layer is dropped: it pins <body> with
//      `position: fixed; overflow: hidden` so the standalone PWA cannot
//      rubber-band, which on a canvas would make every page unscrollable.
//      The one base rule components rely on (buttons inherit font) stays.
//   3. Fonts are imported from fonts/fonts.css rather than @fontsource, so the
//      woff2 files ship as files instead of base64 inside the CSS.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const SRC = new URL("../src/index.css", import.meta.url);
const OUT = new URL("./.cache/ds.css", import.meta.url);
mkdirSync(new URL("./.cache/", import.meta.url), { recursive: true });
let css = readFileSync(SRC, "utf8");

function must(before, after, label) {
  if (!css.includes(before)) throw new Error(`web/src/index.css changed shape (${label}); update gen-css.mjs`);
  css = css.split(before).join(after);
}

// 3. fonts
css = css.replace(/^@import "@fontsource[^\n]*\n/gm, "");
must(
  '@import "tailwindcss";\n',
  '@import "tailwindcss";\n@source "../../src";\n@source "../previews";\n@source "../ds";\n',
  "tailwind import",
);

// 1. theme scoping
must(":root,\n:root[data-theme=\"dark\"] {", ":root,\n[data-theme=\"dark\"] {", "dark token block");
must(":root[data-theme=\"light\"] {", "[data-theme=\"light\"] {", "light token block");
must(":root[data-theme=\"light\"] .gate-field", "[data-theme=\"light\"] .gate-field", "gate-field light");

// 2. base layer: cut the whole block by brace matching, put back what components need
const start = css.indexOf("@layer base {");
if (start < 0) throw new Error("index.css changed shape: no @layer base");
let depth = 0;
let end = -1;
for (let i = css.indexOf("{", start); i < css.length; i++) {
  if (css[i] === "{") depth++;
  else if (css[i] === "}" && --depth === 0) {
    end = i + 1;
    break;
  }
}
css =
  css.slice(0, start) +
  `@layer base {
  button {
    font: inherit;
    color: inherit;
    cursor: pointer;
  }
  ::-webkit-scrollbar {
    display: none;
  }
}` +
  css.slice(end);

writeFileSync(OUT, `/* Generated from web/src/index.css by gen-css.mjs. Do not edit. */\n${css}`);
console.log("ds.css written", css.length, "bytes");
