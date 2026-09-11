import { describe, expect, it } from "vitest";
import css from "../index.css?raw";
import { TOKENS, type Theme } from "./contrast";

/**
 * One theme's declarations, `:root[data-theme="…"] { … }`. The dark block is
 * the second selector of a two-selector rule, which this still finds; it holds
 * for as long as no comment inside a palette block contains a `}`.
 */
function themeBlock(theme: Theme): string {
  const selector = css.indexOf(`:root[data-theme="${theme}"]`);
  if (selector === -1) throw new Error(`index.css has no ${theme} theme block`);
  const open = css.indexOf("{", selector);
  return css.slice(open, css.indexOf("}", open));
}

describe("contrast tokens", () => {
  // `contrast.ts` restates the palette instead of parsing it, and a comment
  // asking two files to be kept in step cannot fail a build. This is what does:
  // a token edited in index.css and not here fails on the next run, rather than
  // leaving the ratio tests measuring colours the app has stopped using —
  // which is the same silent staleness that let a 1.77:1 label ship.
  it("restate index.css verbatim, theme by theme", () => {
    expect(css.length).toBeGreaterThan(0);
    for (const theme of ["dark", "light"] as const) {
      const block = themeBlock(theme);
      for (const [name, value] of Object.entries(TOKENS[theme])) {
        expect(block).toContain(`--${name}: ${value};`);
      }
    }
  });
});
