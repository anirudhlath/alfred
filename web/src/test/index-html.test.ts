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

  it("asks iOS for a standalone app with a translucent status bar", () => {
    expect(html).toContain('<meta name="apple-mobile-web-app-capable" content="yes" />');
    expect(html).toContain(
      '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />',
    );
    expect(html).toContain('<meta name="apple-mobile-web-app-title" content="Alfred" />');
  });

  it("keeps the manifest link untouched for phase 4", () => {
    expect(html).toContain('<link rel="manifest" href="/manifest.json" />');
  });
});
