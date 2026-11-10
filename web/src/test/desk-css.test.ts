import { describe, expect, it } from "vitest";
// The desk is a grid only `index.css` draws, and jsdom lays out none of it: every
// component test passes with these rules deleted. So the shipped text is read instead,
// as `contrast.test.ts` reads the palette.
import css from "../index.css?raw";
import layout from "../shell/layout.ts?raw";

const flat = css.replace(/\s+/g, " ");

/** A rule's `grid-template-columns`, from the selector that opens its block. */
function template(selector: string): string {
  const start = flat.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`index.css has no ${selector}`);
  const block = flat.slice(start, flat.indexOf("}", start));
  const columns = /grid-template-columns: ([^;]+);/.exec(block)?.[1];
  if (!columns) throw new Error(`${selector} sets no columns`);
  return columns;
}

/** A track list split at its top-level spaces, so a `minmax(…)` stays one track. */
function tracks(list: string): string[] {
  const out = [""];
  let depth = 0;
  for (const char of list) {
    if (char === "(") depth += 1;
    if (char === ")") depth -= 1;
    if (char === " " && depth === 0) out.push("");
    else out[out.length - 1] += char;
  }
  return out;
}

/** A track's least width in px: a fixed track's own, or a `minmax`'s first argument. */
function least(track: string): number {
  const px = /^(?:minmax\()?(\d+)px/.exec(track)?.[1];
  if (!px) throw new Error(`no least width in px: ${track}`);
  return Number(px);
}

/** A breakpoint from `layout.ts`, so this follows the query it guards. */
function breakpoint(name: string): number {
  const px = new RegExp(`${name} = "\\(min-width: (\\d+)px\\)"`).exec(layout)?.[1];
  if (!px) throw new Error(`layout.ts has no ${name}`);
  return Number(px);
}

const OPEN = '.desk[data-layout="columns"][data-detail]';

describe("the desk's grid", () => {
  it("hides the line, the bench and the detail until a width asks for them", () => {
    // The phone's default. Without it the phone would lay all three out under the Room.
    expect(flat).toContain(".desk-line, .desk-bench, .desk-detail { display: none; }");
  });

  it("opens the third column at §11's widths, with the bench's minimum held to the page (R3)", () => {
    expect(template(OPEN)).toBe(
      "420px 1px minmax(min(380px, calc(100vw - 782px)), 1fr) 1px minmax(360px, 520px)",
    );
  });

  it("takes from the bench exactly what the other four columns need", () => {
    const [room, line, bench, second, detail] = tracks(template(OPEN));
    const others = least(room) + least(line) + least(second) + least(detail);
    expect(others).toBe(782);
    const [, own, taken] = (
      /^minmax\(min\((\d+)px, calc\(100vw - (\d+)px\)\), 1fr\)$/.exec(bench) ?? []
    ).map(Number);
    // The `calc` is the viewport less the four others, so it has to follow them.
    expect(taken).toBe(others);

    // At the breakpoint, the narrowest the bench gets: the 318 px the docs measure.
    const columns = breakpoint("COLUMNS_QUERY");
    expect(columns).toBe(1100);
    expect(columns - others).toBe(318);
    // And the reason for the `min()`: §11's own 380 does not fit there.
    expect(others + own).toBeGreaterThan(columns);
  });

  it("leaves the bench a column from the first width that shows one", () => {
    const [room, line] = tracks(template('.desk[data-layout="columns"]'));
    expect(breakpoint("SHEET_OVER_BENCH_QUERY") - least(room) - least(line)).toBeGreaterThan(0);
  });
});
