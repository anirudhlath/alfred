import { describe, expect, it } from "vitest";
import { ringFill, ringText, STREAM_INFO, STREAMS } from "@/lib/streams";
import css from "../index.css?raw";
import { contrast, luminance, TOKENS, token, type Theme } from "./contrast";

/**
 * One theme's declarations, `:root[data-theme="…"] { … }`. The dark block is
 * the second selector of a two-selector rule, which this still finds.
 *
 * Comments are stripped before the block is sliced. Both palette blocks now
 * carry paragraphs of prose about the tokens in them, and a single `}` written
 * inside one — in a code fragment, in an aside — would end the block early,
 * leaving the verbatim guard below checking whichever tokens happened to come
 * first and still passing. `source` is a parameter so that case is tested
 * rather than trusted.
 */
function themeBlock(theme: Theme, source: string = css): string {
  const bare = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const selector = bare.indexOf(`:root[data-theme="${theme}"]`);
  if (selector === -1) throw new Error(`index.css has no ${theme} theme block`);
  const open = bare.indexOf("{", selector);
  return bare.slice(open, bare.indexOf("}", open));
}

/**
 * `ringText()` as a browser would resolve it. jsdom resolves no `var()`, and
 * the lightness is a per-theme token, so the theme's own `--ring-text-l` is
 * substituted into the string the function really returns — rather than the
 * number being written down a second time here, where it could drift.
 */
function resolvedRingText(theme: Theme, hue: number): string {
  return ringText(hue).replace("var(--ring-text-l)", token(theme, "ring-text-l"));
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

  it("reads a whole palette block even when the prose in it carries a brace", () => {
    const stylesheet = [
      ':root[data-theme="light"] {',
      "  --bg: #F6F3EE;",
      "  /* A closing brace } in an aside, which is what a block of prose invites. */",
      "  --fg: #221F1B;",
      "}",
    ].join("\n");
    const block = themeBlock("light", stylesheet);
    expect(block).toContain("--bg: #F6F3EE;");
    expect(block).toContain("--fg: #221F1B;");
  });
});

describe("luminance", () => {
  it("reads the short hex the call sites actually write", () => {
    // `#fff` is what `EventRow`, `WhySheet` and `StreamChips` put on a
    // monogram tile. A test written against the real colour must not fail
    // because this helper could not read it.
    expect(luminance("#fff")).toBeCloseTo(luminance("#ffffff"), 12);
    expect(luminance("#25f")).toBeCloseTo(luminance("#2255ff"), 12);
  });

  it("still refuses a syntax the palette does not use", () => {
    expect(() => luminance("rgb(255 255 255)")).toThrow(/cannot read the colour/);
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

    it(`${theme}: a field's placeholder reads in the field it sits in`, () => {
      expect(contrast(token(theme, "fg2"), token(theme, "field"))).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("styles ::placeholder rather than leaving it to preflight", () => {
    // Tailwind's preflight gives `::placeholder` `color-mix(in oklab,
    // currentcolor 50%, transparent)` — `--fg` at half strength over
    // `--field`, which is 3.84:1 dark and 3.22:1 light. Both under AA, and the
    // Composer's offline placeholder is the only thing on screen that says a
    // typed message will be queued rather than lost. Asserted here because the
    // ratio above measures `--fg2`, and a ratio measuring a colour the
    // stylesheet does not use proves nothing at all.
    expect(css).toMatch(/input::placeholder\s*\{[^}]*color:\s*var\(--fg2\);/);
  });
});

/**
 * The eight stream hues. `lib/streams.ts` claims white on `ringFill` is 5.11:1
 * at worst and `ringText` 6.76:1 dark / 4.62:1 light; every one of those was
 * exact and none of them was measured — `streams.test.ts` and
 * `StreamChips.test.tsx` assert the generated strings, which is precisely the
 * failure mode this file was built to stop. Light clears AA by 0.12.
 */
describe("the stream ring", () => {
  it("carries white on every monogram tile and soloed chip", () => {
    for (const name of STREAMS) {
      expect(contrast("#fff", ringFill(STREAM_INFO[name].hue))).toBeGreaterThanOrEqual(4.5);
    }
  });

  for (const theme of ["dark", "light"] as const) {
    it(`${theme}: reads as a chip's own label on the page`, () => {
      for (const name of STREAMS) {
        expect(
          contrast(resolvedRingText(theme, STREAM_INFO[name].hue), token(theme, "bg")),
        ).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
});
