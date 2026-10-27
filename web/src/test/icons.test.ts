import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { srgbHex, token } from "./contrast";

/**
 * The icons are the one part of this client nobody will ever read again: three
 * binary files that only a phone renders, from an SVG no browser here can show.
 * So these tests decode the PNGs and look at the pixels. A header read — which
 * is all this file did at first — passes on a PNG rasterised from a completely
 * different drawing, which is the failure that matters, because iOS never sees
 * `icon.svg` at all. Everything asserted below is a property of the shipped
 * bytes, not of the source they were supposed to come from.
 */

// `new URL("…", import.meta.url)` is Vite's asset syntax, not Node's: the plugin
// rewrites a literal into a `data:` URL of the file's *current* bytes and a
// template into an `import.meta.glob` lookup that resolves to `src/test/undefined`.
// Both are unreadable by `node:fs`, and the second would still be unreadable once
// the PNGs exist. So resolve the directory once, off `import.meta.url` alone.
const PUBLIC_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../../public");

/**
 * The mark's two colours, converted from the tokens rather than restated. The
 * chain is `index.css` → `contrast.ts`'s `TOKENS` (which `contrast.test.ts`
 * holds to the stylesheet verbatim) → here → the SVG's literal and the PNGs'
 * pixels. Retuning `--accent` the sanctioned way therefore fails this file until
 * the icon is redrawn and re-rendered; before, the icon stayed peach in silence.
 */
const GROUND = srgbHex(token("dark", "bg")); // #25221F
const ACCENT = srgbHex(token("dark", "accent")); // #F79E75

/** Every raster icon the repo ships, named once so a fourth is added in one place. */
const ICONS = [
  ["apple-touch-icon.png", 180],
  ["icon-192.png", 192],
  ["icon-512.png", 512],
] as const;

// The PNG header is fixed-layout (spec §11.2.2 IHDR), so these need no decoder.
const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const IHDR_WIDTH = 16;
const IHDR_HEIGHT = 20;
const IHDR_BIT_DEPTH = 24;
const IHDR_COLOUR_TYPE = 25;
const IHDR_INTERLACE = 28;
/** Colour type 2 is truecolour. 6 is truecolour *with* an alpha channel. */
const TRUECOLOUR = 2;
const EIGHT_BIT = 8;
const NOT_INTERLACED = 0;

/** A maskable icon's safe circle: 40% of the canvas, measured from the centre. */
const MASKABLE_SAFE_FRACTION = 0.4;
/**
 * What the committed renders actually measure, with room for a rasteriser's
 * rounding: ground 89.3–89.8%, accent 9.1–9.7%, the rest an antialias fringe,
 * and the furthest inked pixel 0.333 of the canvas from the centre. Wide enough
 * not to be brittle, tight enough that a blank square, a different glyph or a
 * mark stretched past the safe circle all fall outside.
 */
const GROUND_SHARE = { min: 0.88, max: 0.91 };
const ACCENT_SHARE = { min: 0.085, max: 0.105 };
const MARK_REACH = { min: 0.25, max: MASKABLE_SAFE_FRACTION };

interface Png {
  signature: Buffer;
  width: number;
  height: number;
  bitDepth: number;
  colourType: number;
  interlace: number;
  chunks: string[];
  /** `#RRGGBB` at a fraction of the canvas, so one assertion reads on all three sizes. */
  at: (xFraction: number, yFraction: number) => string;
  /** `#RRGGBB` → how many pixels hold it. */
  census: Map<string, number>;
  /** Distance of the furthest non-ground pixel from the centre, as a fraction of the width. */
  reach: number;
}

/**
 * Enough of a PNG reader for a non-interlaced 8-bit truecolour file, which is
 * all this repo ships and all the header assertions permit. `node:zlib` is a
 * built-in, so the alternative — shelling out to `rsvg-convert` — is avoided:
 * it does not exist in CI, and a test that silently skips is not a test.
 */
function decode(name: string): Png {
  const bytes = readFileSync(join(PUBLIC_DIR, name));
  const signature = bytes.subarray(0, SIGNATURE.length);
  // Thrown rather than asserted: this runs while the suite is being collected, and a
  // file that is not a PNG cannot be walked for chunks at all. The signature is
  // asserted properly in the first test below.
  if (!signature.equals(SIGNATURE)) throw new Error(`${name} is not a PNG`);

  const chunks: string[] = [];
  const idat: Buffer[] = [];
  let ihdr: Buffer | undefined;
  for (let at = SIGNATURE.length; at + 8 <= bytes.length; ) {
    const length = bytes.readUInt32BE(at);
    const type = bytes.toString("ascii", at + 4, at + 8);
    const data = bytes.subarray(at + 8, at + 8 + length);
    chunks.push(type);
    if (type === "IHDR") ihdr = data;
    if (type === "IDAT") idat.push(data);
    at += 12 + length; // length + type + data + CRC
  }
  if (!ihdr) throw new Error(`${name} has no IHDR chunk`);

  const width = bytes.readUInt32BE(IHDR_WIDTH);
  const height = bytes.readUInt32BE(IHDR_HEIGHT);
  const stride = width * 3;
  const raw = inflateSync(Buffer.concat(idat));
  const pixels = Buffer.alloc(height * stride);
  // Undo the per-scanline filters (PNG spec §9.2). Each row is prefixed with the
  // filter type its bytes were encoded under, and every filter is relative to the
  // pixel to the left (a), the one above (b) and the one up-left (c).
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const row = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let i = 0; i < stride; i++) {
      const a = i >= 3 ? pixels[y * stride + i - 3] : 0;
      const b = y > 0 ? pixels[(y - 1) * stride + i] : 0;
      const c = i >= 3 && y > 0 ? pixels[(y - 1) * stride + i - 3] : 0;
      let value = row[i];
      if (filter === 1) value += a;
      else if (filter === 2) value += b;
      else if (filter === 3) value += (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const [pa, pb, pc] = [Math.abs(p - a), Math.abs(p - b), Math.abs(p - c)];
        value += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      pixels[y * stride + i] = value & 255;
    }
  }

  const hex = (offset: number) =>
    `#${[pixels[offset], pixels[offset + 1], pixels[offset + 2]]
      .map((channel) => channel.toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase()}`;

  const census = new Map<string, number>();
  let furthest = 0;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const colour = hex(y * stride + x * 3);
      census.set(colour, (census.get(colour) ?? 0) + 1);
      if (colour !== GROUND) {
        furthest = Math.max(furthest, Math.hypot(x + 0.5 - width / 2, y + 0.5 - height / 2));
      }
    }
  }

  return {
    signature,
    width,
    height,
    bitDepth: bytes.readUInt8(IHDR_BIT_DEPTH),
    colourType: bytes.readUInt8(IHDR_COLOUR_TYPE),
    interlace: bytes.readUInt8(IHDR_INTERLACE),
    chunks,
    at: (xFraction, yFraction) =>
      hex(Math.round(yFraction * height) * stride + Math.round(xFraction * width) * 3),
    census,
    reach: furthest / width,
  };
}

describe.each(ICONS)("%s", (name, size) => {
  const image = decode(name);
  const total = image.width * image.height;

  it(`is a ${size}px square, 8-bit, non-interlaced truecolour PNG`, () => {
    expect(image.signature).toEqual(SIGNATURE);
    expect([image.width, image.height]).toEqual([size, size]);
    expect(image.bitDepth).toBe(EIGHT_BIT);
    expect(image.colourType).toBe(TRUECOLOUR);
    expect(image.interlace).toBe(NOT_INTERLACED);
  });

  it("carries no transparency, by colour type or by tRNS", () => {
    // iOS does not composite transparency on a home-screen tile — it fills it
    // black — so a hole anywhere here is a black patch on the phone (spec §4.2).
    // Colour type 2 alone does not say so: a `tRNS` chunk makes a nominated
    // colour fully transparent in a truecolour PNG, and would leave the header
    // looking exactly as it does now.
    expect(image.colourType).toBe(TRUECOLOUR);
    expect(image.chunks).not.toContain("tRNS");
  });

  it("is a whole file, not a truncated one", () => {
    expect(image.chunks).toContain("IDAT");
    expect(image.chunks.at(-1)).toBe("IEND");
  });

  it("is drawn in the two token colours and nothing else", () => {
    // The census is what ties these bytes to `icon.svg`: a PNG re-rendered from
    // a different drawing — another ground, another glyph, another font — passes
    // every header check and fails here.
    const ground = (image.census.get(GROUND) ?? 0) / total;
    const accent = (image.census.get(ACCENT) ?? 0) / total;
    expect(ground).toBeGreaterThanOrEqual(GROUND_SHARE.min);
    expect(ground).toBeLessThanOrEqual(GROUND_SHARE.max);
    expect(accent).toBeGreaterThanOrEqual(ACCENT_SHARE.min);
    expect(accent).toBeLessThanOrEqual(ACCENT_SHARE.max);

    // And every remaining pixel is a blend of those two, never a third colour:
    // each channel lies between the two ends, give or take a rounding step.
    const ends = [GROUND, ACCENT].map((colour) => [1, 3, 5].map((i) => parseInt(colour.slice(i, i + 2), 16)));
    const strangers = [...image.census.keys()].filter((colour) =>
      [1, 3, 5].some((i, channel) => {
        const value = parseInt(colour.slice(i, i + 2), 16);
        const [low, high] = [ends[0][channel], ends[1][channel]].sort((x, y) => x - y);
        return value < low - 1 || value > high + 1;
      }),
    );
    expect(strangers).toEqual([]);
  });

  it("keeps the whole mark inside the maskable safe circle", () => {
    // 512 is marked `any maskable`, so the art has to survive a mask that keeps
    // only the middle 40%. The other two are the same drawing, so they are held
    // to the same reach — and the lower bound is what stops a mark shrunk to a
    // dot, or lost altogether, from passing as "inside the circle".
    expect(image.reach).toBeGreaterThan(MARK_REACH.min);
    expect(image.reach).toBeLessThan(MARK_REACH.max);
  });

  it("reads as a capital A down the middle: apex, counter, crossbar, open feet", () => {
    // A vertical scan through the centre of the glyph. This is the cheapest
    // statement of "the mark is an A" that a rasteriser cannot fake: a letter
    // drawn in another font, or another letter, does not alternate this way.
    expect(image.at(0.5, 0.32)).toBe(ACCENT); // the apex stroke
    expect(image.at(0.5, 0.55)).toBe(GROUND); // the counter, the triangular hole
    expect(image.at(0.5, 0.617)).toBe(ACCENT); // the crossbar
    expect(image.at(0.5, 0.7)).toBe(GROUND); // between the legs, below the bar
  });

  it("is full-bleed: the corners are the ground, not a masked edge", () => {
    expect(image.at(0, 0)).toBe(GROUND);
    expect(image.at(0.99, 0)).toBe(GROUND);
    expect(image.at(0, 0.99)).toBe(GROUND);
    expect(image.at(0.99, 0.99)).toBe(GROUND);
  });
});

describe("icon.svg", () => {
  const source = readFileSync(join(PUBLIC_DIR, "icon.svg"), "utf8");
  // Comments are prose and must not be searched as if they were markup: the
  // file explains why the mark is not a `<text>` element, and a grep for that
  // string finds the explanation. The replace runs until nothing changes, so no
  // comment can be left behind spliced together from the pieces of others.
  let markup = source;
  let previous: string;
  do {
    previous = markup;
    markup = markup.replace(/<!--[\s\S]*?-->/g, "");
  } while (markup !== previous);

  /** A tag's attributes, so an assertion does not depend on the order they are written in. */
  const attributes = (tag: string): Record<string, string> =>
    Object.fromEntries(
      [...tag.matchAll(/([\w:-]+)="([^"]*)"/g)].map(([, name, value]) => [name, value]),
    );
  const tag = (pattern: RegExp) => {
    const found = pattern.exec(markup);
    if (!found) throw new Error(`icon.svg has no ${pattern}`);
    return attributes(found[0]);
  };

  it("is a full-bleed square with no rounded corners and no mask", () => {
    // iOS masks the tile itself. Anything outside a rounded rect is transparent,
    // and transparent composites to black, so a radius here is black slivers in
    // the corners of the home-screen icon. `rx` is one way to draw one: a
    // `clipPath` or a `mask` is the same defect spelled differently.
    expect(tag(/<svg\b[^>]*>/).viewBox).toBe("0 0 512 512");
    const rect = tag(/<rect\b[^>]*>/);
    expect(rect).toMatchObject({ width: "512", height: "512", fill: GROUND });
    expect(rect.rx).toBeUndefined();
    expect(rect.ry).toBeUndefined();
    expect(markup).not.toMatch(/\b(rx|ry|clip-path|mask)=|<(clipPath|mask)\b/);
  });

  it("keeps its comments XML-legal, so librsvg can still read it", () => {
    // XML forbids `--` inside a comment, and the comments in this file are long
    // prose that names tokens. Writing one of those token names into a comment
    // is a parse error, not a warning: librsvg refuses the file outright, so the
    // next regeneration fails and every browser that takes the SVG favicon —
    // which is all of them but iOS — shows nothing. The structural assertions
    // around this one all pass on a file that no parser will accept, because
    // they are regexes; this is the one that would not.
    for (const [, body] of source.matchAll(/<!--([\s\S]*?)-->/g)) {
      expect(body).not.toContain("--");
    }
    expect(source.split("<!--")).toHaveLength(source.split("-->").length);
  });

  it("draws the mark as geometry, not as text", () => {
    // A <text> icon renders with whatever font the rasteriser happens to have.
    // DM Sans ships woff2 only, which no SVG rasteriser on this box can load.
    expect(markup).not.toMatch(/<text\b/);
    expect(markup).not.toMatch(/font-family/);
    expect([...markup.matchAll(/<path\b[^>]*>/g)]).toHaveLength(2);
  });

  it("takes its colour from the group, so the two strokes cannot drift apart", () => {
    // oklch(0.78 0.12 45) — `--accent`, via contrast.ts — converted, because
    // librsvg 2.62 does not parse oklch(). A `stroke` or `fill` on a path would
    // override this silently, and the PNG census is the only thing that would
    // notice, so say it here too.
    expect(tag(/<g\b[^>]*>/).stroke).toBe(ACCENT);
    for (const path of markup.matchAll(/<path\b[^>]*>/g)) {
      expect(attributes(path[0]).stroke).toBeUndefined();
      expect(attributes(path[0]).fill).toBeUndefined();
    }
  });
});
