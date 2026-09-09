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
    const viewport = /<meta name="viewport" content="([^"]+)"/.exec(html)?.[1] ?? "";
    expect(viewport).toContain("width=device-width");
    expect(viewport).toContain("initial-scale=1");
    expect(viewport).toContain("viewport-fit=cover");
    expect(viewport).toContain("interactive-widget=resizes-content");
  });

  it("does not disable pinch zoom", () => {
    expect(html).not.toContain("user-scalable=no");
    expect(html).not.toContain("maximum-scale=1");
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
