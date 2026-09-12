import { describe, expect, it } from "vitest";
import css from "../index.css?raw";
import { contrast, TOKENS, token, type Theme } from "./contrast";

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

/**
 * The ratios themselves, for the pairs no rendered test can check: jsdom
 * resolves no `var()`, so a component asserting on `var(--accent-text)` proves
 * only that the string was written down. These assert the colours behind the
 * strings, in both themes, and a palette edit that breaks one fails here.
 */
describe("contrast ratios", () => {
  for (const theme of ["dark", "light"] as const) {
    it(`${theme}: the accent reads as text on the page`, () => {
      // The three accent-as-text call sites — the Workshop's `Room`, EventRow's
      // `Why · causal thread`, a sheet's `Done` — all sit on --bg at 15 px or
      // 13 px, so AA's 4.5:1 is the bar. --accent itself is 2.34:1 on paper,
      // which is what --accent-text exists to fix.
      expect(contrast(token(theme, "accent-text"), token(theme, "bg"))).toBeGreaterThanOrEqual(4.5);
    });

    it(`${theme}: the chosen bench is visible on the switcher's track`, () => {
      // A fill, not text, so WCAG has no ratio to hold it to; 1.1:1 is the
      // floor that the old dark palette failed at exactly 1.00:1, --field and
      // --surface having been the same colour.
      expect(contrast(token(theme, "field"), token(theme, "surface"))).toBeGreaterThan(1.1);
      // And the bench's own label on that fill is text again.
      expect(contrast(token(theme, "fg"), token(theme, "field"))).toBeGreaterThanOrEqual(4.5);
    });
  }
});
