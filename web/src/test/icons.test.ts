import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// `new URL("…", import.meta.url)` is Vite's asset syntax, not Node's: the plugin
// rewrites a literal into a `data:` URL of the file's *current* bytes and a
// template into an `import.meta.glob` lookup that resolves to `src/test/undefined`.
// Both are unreadable by `node:fs`, and the second would still be unreadable once
// the PNGs exist. So resolve the directory once, off `import.meta.url` alone.
const PUBLIC_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../../public");

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function png(name: string) {
  const bytes = readFileSync(join(PUBLIC_DIR, name));
  return {
    signature: bytes.subarray(0, 8),
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
    colourType: bytes.readUInt8(25),
  };
}

describe("the icon PNGs", () => {
  it.each([
    ["apple-touch-icon.png", 180],
    ["icon-192.png", 192],
    ["icon-512.png", 512],
  ])("%s is a %ipx square PNG", (name, size) => {
    const image = png(name);
    expect(image.signature).toEqual(PNG_SIGNATURE);
    expect(image.width).toBe(size);
    expect(image.height).toBe(size);
  });

  it.each(["apple-touch-icon.png", "icon-192.png", "icon-512.png"])(
    "%s carries no alpha channel",
    (name) => {
      // Colour type 2 is truecolour; 6 is truecolour + alpha. iOS does not
      // composite transparency on a home-screen tile — it fills it black — so
      // an alpha channel here is a black corner on the phone (spec §4.2).
      expect(png(name).colourType).toBe(2);
    },
  );
});

describe("icon.svg", () => {
  const svg = readFileSync(join(PUBLIC_DIR, "icon.svg"), "utf8");

  it("is a full-bleed square with no rounded corners", () => {
    // iOS masks the tile itself. Anything outside a rounded rect is transparent,
    // and transparent composites to black, so a radius here is black slivers in
    // the corners of the home-screen icon.
    expect(svg).toContain('viewBox="0 0 512 512"');
    expect(svg).toContain('<rect width="512" height="512" fill="#25221F"/>');
    expect(svg).not.toContain("rx=");
  });

  it("draws the mark as geometry, not as text", () => {
    // A <text> icon renders with whatever font the rasteriser happens to have.
    // DM Sans ships woff2 only, which no SVG rasteriser on this box can load.
    expect(svg).not.toContain("<text");
    expect(svg).not.toContain("font-family");
  });

  it("uses the dark theme's accent, as sRGB hex", () => {
    // oklch(0.78 0.12 45) — src/index.css:38 — converted, because librsvg 2.62
    // does not parse oklch().
    expect(svg).toContain('stroke="#F79E75"');
  });
});
