/**
 * WCAG contrast for the design tokens, so a pair the eye cannot check in jsdom
 * can still be held to a ratio by a test. jsdom resolves no custom property and
 * computes no colour: an assertion on a rendered `var(--x)` proves only that
 * the string was written down, which is how a 1.77:1 label shipped once.
 *
 * Two colour syntaxes, which is what the palette uses: `#rrggbb` and
 * `oklch(L C H)`. Anything else throws rather than guessing — a token in a
 * third syntax should fail loudly here, not silently pass.
 */

export type Theme = "dark" | "light";

/**
 * The tokens this helper knows, restated from `src/index.css` — which is the
 * source of truth, and carries a pointer back here. Not read off the file:
 * vitest stubs every `.css` import to an empty string, and reading the disk
 * needs node builtins, which `tsconfig.app.json` (`types: ["vite/client"]`)
 * does not typecheck under `src`. Add a pair here when a test needs one, and
 * keep the two files in step.
 */
const TOKENS: Record<Theme, Record<string, string>> = {
  dark: { accent: "oklch(0.78 0.12 45)", "on-accent": "#25221F" },
  light: { accent: "oklch(0.72 0.13 45)", "on-accent": "#221F1B" },
};

/** What a token holds in one theme: `token("dark", "accent")` → `oklch(0.78 0.12 45)`. */
export function token(theme: Theme, name: string): string {
  const value = TOKENS[theme][name];
  if (value === undefined) throw new Error(`contrast.ts does not carry --${name}; copy it from index.css`);
  return value;
}

/** sRGB transfer function, channel by channel (WCAG 2.x, relative luminance). */
function toLinear(channel: number): number {
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

/** Linear-light sRGB of `oklch(L C H)` — the Oklab matrices, clamped into gamut. */
function oklchToLinear(l: number, c: number, hDeg: number): [number, number, number] {
  const h = (hDeg * Math.PI) / 180;
  const a = c * Math.cos(h);
  const b = c * Math.sin(h);
  const long = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const med = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const short = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  return [
    clamp(4.0767416621 * long - 3.3077115913 * med + 0.2309699292 * short),
    clamp(-1.2684380046 * long + 2.6097574011 * med - 0.3413193965 * short),
    clamp(-0.0041960863 * long - 0.7034186147 * med + 1.707614701 * short),
  ];
}

/** Relative luminance of `#rrggbb` or `oklch(L C H)`. */
export function luminance(color: string): number {
  const value = color.trim();
  if (/^#[0-9a-f]{6}$/i.test(value)) {
    const int = Number.parseInt(value.slice(1), 16);
    const [r, g, b] = [(int >> 16) & 255, (int >> 8) & 255, int & 255].map((c) => toLinear(c / 255));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  const parts = /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)$/i.exec(value);
  if (!parts) throw new Error(`contrast.ts cannot read the colour "${value}"`);
  const [r, g, b] = oklchToLinear(Number(parts[1]), Number(parts[2]), Number(parts[3]));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The WCAG ratio between two colours, 1 to 21. Which one is the text does not matter. */
export function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}
