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

    it(`${theme}: green reads as a word, not only as a dot`, () => {
      // The Memory bench is the first screen to put green in words — a rising
      // routine's trend line and the `model: ok` pill, both 11 px on --bg.
      // --green itself is 6.25:1 on ink but 2.29:1 on paper, the same trap
      // --accent-text exists for, so the text sites take --green-text and
      // --green stays what fills the Door's `applied` dot.
      expect(contrast(token(theme, "green-text"), token(theme, "bg"))).toBeGreaterThanOrEqual(4.5);
    });

    it(`${theme}: the accent reads as text on a card as well as on the page`, () => {
      // `Show all` on a semantic card is the first --accent-text on --surface
      // (`MemoryBench.tsx`). It clears AA at 4.85:1 in light — but the pair had
      // never been measured, and an unmeasured pair is how a 2.34:1 back button
      // shipped on this branch.
      expect(contrast(token(theme, "accent-text"), token(theme, "surface"))).toBeGreaterThanOrEqual(
        4.5,
      );
    });

    it(`${theme}: a filled dark control carries its own label`, () => {
      // --ink filled with --paper on it: the Memory bench's chosen sub-tab pill
      // (handoff §6) and the Activity bench's Pause button. The two tokens swap
      // with the theme, so the ratio holds either way round.
      expect(contrast(token(theme, "paper"), token(theme, "ink"))).toBeGreaterThanOrEqual(4.5);
    });

    it(`${theme}: a field's placeholder reads in the field it sits in`, () => {
      expect(contrast(token(theme, "fg2"), token(theme, "field"))).toBeGreaterThanOrEqual(4.5);
    });

    it(`${theme}: the label on a filled accent button reads`, () => {
      // The Activity bench's Resume button is the one of these on this branch:
      // `--on-accent` on `--accent` while the feed is held
      // (`workshop/ActivityBench.tsx`). The pair exists because `--ink` is
      // near-white in the dark theme, which is the first paint, and near-white
      // on the accent is 1.77:1. That the button asks for the right token is
      // `ActivityBench.test.tsx`'s to check; this is the ratio behind it.
      expect(contrast(token(theme, "on-accent"), token(theme, "accent"))).toBeGreaterThanOrEqual(4.5);
    });

    it(`${theme}: a switch shows which way it is set`, () => {
      // The Workshop's switch (`workshop/Switch.tsx`). WCAG 1.4.11 asks 3:1 of
      // two things here and neither is text: the boundary that makes the
      // control findable, and the knob that says which way it is set. The
      // handoff's own pairing fails both — a `--bg` knob on a `--line` track is
      // 1.18:1 in light, and the track's edge against the page is the same
      // 1.18:1 — so the track takes an inset `--muted` edge and the knob takes
      // the token that reads on whichever track it is on.
      //
      // The inset edge, not the track fill, is the outermost pixel of the
      // control, so it is the boundary the ratio is owed for. The raw `--accent`
      // ON track is 2.17:1 on a card in light and is *not* that boundary.
      expect(contrast(token(theme, "muted"), token(theme, "bg"))).toBeGreaterThanOrEqual(3);
      expect(contrast(token(theme, "on-accent"), token(theme, "accent"))).toBeGreaterThanOrEqual(3);
      expect(contrast(token(theme, "fg2"), token(theme, "line"))).toBeGreaterThanOrEqual(3);
    });

    it(`${theme}: a control on a card is bounded against the card, not the page`, () => {
      // The System bench puts the same switch, and four expiry chips, inside a
      // `SystemSection` card. `--surface` is a step off `--bg`, so a boundary
      // measured against the page proves nothing here — which is how a
      // seven-line rationale came to be copied onto a surround it had never
      // been run against. The chips' own `--muted` border is this same pair.
      expect(contrast(token(theme, "muted"), token(theme, "surface"))).toBeGreaterThanOrEqual(3);
      // What the card's own hairline would have given instead — and it is the
      // same 1.09:1 in both themes, so it is asserted in both.
      expect(contrast(token(theme, "line"), token(theme, "surface"))).toBeLessThan(1.2);
    });

    it(`${theme}: a health dot says which state it is in without its hue`, () => {
      // `HealthStat`'s dot (`workshop/SystemBench.tsx`) is filled when the
      // reading is alive and an empty `--muted` ring when it is not, which is
      // `MemoryBench`'s store dot exactly. Two *filled* circles would leave hue
      // as the only channel, and `--green` against `--muted` is 1.22:1 dark and
      // 1.51:1 light — no difference at all to a reader who cannot separate the
      // two hues, in either theme.
      expect(contrast(token(theme, "green"), token(theme, "muted"))).toBeLessThan(3);
      // Both states against the card they sit on, which is the ratio 1.4.11
      // actually asks for.
      expect(contrast(token(theme, "green-text"), token(theme, "surface"))).toBeGreaterThanOrEqual(
        3,
      );
      expect(contrast(token(theme, "muted"), token(theme, "surface"))).toBeGreaterThanOrEqual(3);
    });

    it(`${theme}: the spend bar reads as a graphic and not only as a sentence`, () => {
      // `SpendCard`'s bar (`workshop/SystemBench.tsx`) is a `role="img"` whose
      // `aria-labelledby` carries the fact in words — but a low-vision sighted
      // reader gets only the graphic, so the fill needs 3:1 against its track
      // and the track needs 3:1 against the card. The fill takes
      // `--accent-text` and the track an outlined `--muted` edge.
      expect(contrast(token(theme, "accent-text"), token(theme, "line"))).toBeGreaterThanOrEqual(3);
      expect(contrast(token(theme, "muted"), token(theme, "surface"))).toBeGreaterThanOrEqual(3);
      // And once the reads stop landing the fill leaves the accent for `--fg2`:
      // receding here is losing the attention colour, not losing contrast.
      expect(contrast(token(theme, "fg2"), token(theme, "line"))).toBeGreaterThanOrEqual(3);
    });

    it(`${theme}: a service's state word reads on the card it sits on`, () => {
      // The Connected services row prints `ok`, `failed` or `unset` as its own
      // word (`workshop/IntegrationRow.tsx`) — 11 px text on `--surface`, so
      // AA's 4.5:1 is the bar and `--green` itself is nowhere near it. `unset`
      // takes `--fg2` rather than the handoff's `--muted`, which is the pair
      // the light-only test below names.
      expect(contrast(token(theme, "green-text"), token(theme, "surface"))).toBeGreaterThanOrEqual(
        4.5,
      );
      expect(contrast(token(theme, "accent-text"), token(theme, "surface"))).toBeGreaterThanOrEqual(
        4.5,
      );
      expect(contrast(token(theme, "fg2"), token(theme, "surface"))).toBeGreaterThanOrEqual(4.5);
      // And the failed dot's ring, which is a state indicator rather than text
      // and so is owed 3:1 against the card (WCAG 1.4.11).
      expect(contrast(token(theme, "accent-text"), token(theme, "surface"))).toBeGreaterThanOrEqual(
        3,
      );
    });

    it(`${theme}: a credential field is findable, and carries what is typed into it`, () => {
      // The credential form's inputs (`workshop/IntegrationRow.tsx`) sit inside
      // a section card, so the edge that makes them findable is measured
      // against `--surface` and not the page. `--muted`, not `--line`, for the
      // reason the Quiet chips give — 1.09:1 is no edge at all.
      expect(contrast(token(theme, "muted"), token(theme, "surface"))).toBeGreaterThanOrEqual(3);
      expect(contrast(token(theme, "fg"), token(theme, "field"))).toBeGreaterThanOrEqual(4.5);
      // The placeholder, which is the row's `saved` — the whole evidence that
      // something is stored and the field is deliberately empty.
      expect(contrast(token(theme, "fg2"), token(theme, "field"))).toBeGreaterThanOrEqual(4.5);
    });

    it(`${theme}: a pairing code reads at arm's length on a card`, () => {
      // 32 px mono in `--fg` on `--surface` (`workshop/SystemSections.tsx`) —
      // read off this screen and typed into another one. The same pair carries
      // every section's row title and its empty-state sentence.
      expect(contrast(token(theme, "fg"), token(theme, "surface"))).toBeGreaterThanOrEqual(4.5);
    });

    it(`${theme}: a chosen chip carries its own label`, () => {
      // Quiet's expiry chips fill with `--ink` and label in `--paper`, the same
      // pairing the Memory bench's chosen sub-tab uses — and the fill has to be
      // findable on a card, not only on the page.
      expect(contrast(token(theme, "paper"), token(theme, "ink"))).toBeGreaterThanOrEqual(4.5);
      expect(contrast(token(theme, "ink"), token(theme, "surface"))).toBeGreaterThanOrEqual(3);
      // And an unchosen one is text on the card it sits on.
      expect(contrast(token(theme, "fg2"), token(theme, "surface"))).toBeGreaterThanOrEqual(4.5);
    });
  }

  /**
   * Light is the theme every one of these pairs fails in, and it is a live
   * runtime state (`lib/theme.ts`), not a hypothetical. Asserted on its own
   * rather than inside the loop above, where the dark values pass and would
   * make the claim vacuous — and asserted at all because a comment saying "the
   * raw token would not have done" cannot fail a build.
   */
  it("light: names the tokens the System bench could not have used", () => {
    // `unset`, if it were the handoff's `--muted` on a card. 3.20:1 — under AA
    // at the 11 px it is drawn at, and this word is the entire state of the
    // row rather than decoration beside it, so it takes `--fg2` instead. (The
    // dark theme clears it at 4.53:1, which is exactly why the claim is made
    // here and not inside the loop above.)
    expect(contrast(token("light", "muted"), token("light", "surface"))).toBeLessThan(4.5);
    // The dot, if it were filled `--green` on a card (`HealthStat`).
    expect(contrast(token("light", "green"), token("light", "surface"))).toBeLessThan(3);
    // The spend fill, if it were the raw accent on its `--line` track.
    expect(contrast(token("light", "accent"), token("light", "line"))).toBeLessThan(3);
    // The switch's ON track against the card, which is why the boundary is the
    // inset `--muted` edge drawn over it rather than the track fill itself.
    expect(contrast(token("light", "accent"), token("light", "surface"))).toBeLessThan(3);
  });

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
