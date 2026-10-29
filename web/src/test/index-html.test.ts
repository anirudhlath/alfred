import { describe, expect, it } from "vitest";
// Read the shipped file rather than a rendered DOM: these metas are never mounted
// by a test that renders components, and losing one is silent until a phone shows it.
// Vite's `?raw` import needs no Node types and works under the jsdom environment,
// where `import.meta.url` is an http: URL rather than a file.
import html from "../../index.html?raw";

describe("index.html", () => {
  it("declares the document language", () => {
    expect(html).toContain('<html lang="en">');
  });

  it("opts into the notch and the resizing keyboard", () => {
    // `[\s\S]*?` and not `[^>]*`: the tag is wrapped over several lines, and the
    // comment above it also mentions `content`, so the match has to start at the tag.
    const viewport = /<meta\s+name="viewport"[\s\S]*?content="([^"]+)"/.exec(html)?.[1] ?? "";
    expect(viewport).toContain("width=device-width");
    expect(viewport).toContain("initial-scale=1");
    expect(viewport).toContain("viewport-fit=cover");
    expect(viewport).toContain("interactive-widget=resizes-content");
  });

  it("locks the scale, because a standalone app is not a page that zooms", () => {
    // iOS ignores both in Safari and honours both in standalone, which is where
    // this runs. The focus-zoom they suppress is also defended against in the
    // fields themselves (16 px, Composer.tsx) and in keyboardInset()'s scale
    // normalisation, so no one of the three is load-bearing on its own.
    const viewport = /<meta\s+name="viewport"[\s\S]*?content="([^"]+)"/.exec(html)?.[1] ?? "";
    expect(viewport).toContain("maximum-scale=1");
    expect(viewport).toContain("user-scalable=no");
  });

  it("ships one theme-color, the dark first-paint token, for applyTheme() to rewrite", () => {
    // The theme is the app's (stored choice, else the hour), never the OS scheme, so
    // a per-scheme `media` pair would disagree with it for anyone whose phone is set
    // the other way. applyTheme() keeps this one meta in step with data-theme.
    expect(html).toContain('<meta name="theme-color" content="#25221F" />');
    expect(html).not.toContain("prefers-color-scheme");
  });

  it("stops iOS turning ids and times into phone links", () => {
    expect(html).toContain('<meta name="format-detection" content="telephone=no" />');
  });

  it("asks iOS for a standalone app", () => {
    expect(html).toContain('<meta name="apple-mobile-web-app-capable" content="yes" />');
    expect(html).toContain('<meta name="apple-mobile-web-app-title" content="Alfred" />');
  });

  it("leaves the status bar opaque, because iOS 27 lays a translucent one out short", () => {
    // Measured on an iPhone 18 Pro Max (956 pt) from the Home Screen with
    // black-translucent: the app is drawn from the top of the screen, under the
    // status bar, but laid out 894 pt tall, and iOS paints nothing of the page
    // in the 62 pt below that, not even a fixed box sized to the screen. With no
    // style the app starts below the bar and is laid out to the bottom of the
    // screen, and the bar takes theme-color.
    expect(html).not.toMatch(/<meta\s[^>]*name="apple-mobile-web-app-status-bar-style"/);
  });

  it("gives iOS a raster home-screen icon, which is the only kind it takes", () => {
    expect(html).toContain('<link rel="apple-touch-icon" href="/apple-touch-icon.png" />');
  });

  it("keeps the SVG favicon for everything that is not an iOS home screen", () => {
    // The raster tile is iOS's alone. A tab, a bookmark and the installed
    // manifest all take the SVG, which is the only one of the four that scales.
    expect(html).toContain('<link rel="icon" href="/icon.svg" />');
  });

  it("links the dark manifest, which applyTheme swaps for the light one", () => {
    // Dark to match the dark `theme-color` two lines up: both are the
    // first-paint values, and both are rewritten within a frame by applyTheme.
    expect(html).toContain('<link rel="manifest" href="/manifest.json" />');
  });
});
