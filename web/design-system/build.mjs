// Builds the upload bundle into ../out:
//   _ds_bundle.js / _ds_bundle.css   the design system (window.Alfred)
//   _vendor/react.js, react-dom.js   React 19 as browser globals (19 ships no UMD)
//   _preview/<Name>.js               one story module per component (window.__dsPreview)
import { copyFileSync, readdirSync, readFileSync, renameSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const OUT = path.join(HERE, "out");
const CACHE = path.join(HERE, ".cache");
const WEB_SRC = path.resolve(HERE, "../src");
const only = process.argv[2]; // "assets" | "lib" | "vendor" | "previews" | undefined (all)

const alias = [
  { find: /^@\/shell\/ThemeProvider$/, replacement: path.join(HERE, "shims/ThemeProvider.tsx") },
  { find: /^@\//, replacement: `${WEB_SRC}/` },
];
const common = {
  configFile: false,
  root: HERE,
  logLevel: "warn",
  resolve: { alias },
  define: { "process.env.NODE_ENV": '"production"' },
};

/**
 * Browser globals, by alias rather than `external`: an external is only mapped to
 * its global for ESM imports, and React's own CommonJS (react-dom requiring
 * react) would be left calling a `require` that does not exist in a browser.
 */
const GLOBAL_SHIMS = {
  react: path.join(HERE, "shims/react-global.cjs"),
  "react-dom": path.join(HERE, "shims/react-dom-global.cjs"),
  "@ds": path.join(HERE, "shims/alfred-global.cjs"),
};

/** Region comments name modules by their path from here; say `web/...` instead. */
function tidyRegions(file) {
  const text = readFileSync(file, "utf8").replace(/(?:\.\.\/)+(?:web\/)?(src|node_modules)\//g, "web/$1/");
  writeFileSync(file, text);
}

async function iife({ entry, name, file, outDir = OUT, globals = [], css = false, minify = false }) {
  const shims = globals.map((g) => ({ find: new RegExp(`^${g}$`), replacement: GLOBAL_SHIMS[g] }));
  await iifeBuild({ entry, name, file, outDir, shims, css, minify });
  tidyRegions(path.join(outDir, file));
}

async function iifeBuild({ entry, name, file, outDir, shims, css, minify }) {
  await build({
    ...common,
    resolve: { alias: [...shims, ...alias] },
    plugins: css ? [react(), tailwindcss()] : [react()],
    build: {
      outDir,
      emptyOutDir: false,
      minify,
      cssMinify: false,
      copyPublicDir: false,
      lib: { entry, name, formats: ["iife"], fileName: () => file, cssFileName: "_ds_bundle" },

    },
  });
}

if (!only || only === "assets") {
  // Fonts from the client's own @fontsource packages, Latin and Latin Extended
  // only, as files rather than base64 inside the CSS.
  const fonts = path.join(OUT, "fonts");
  mkdirSync(fonts, { recursive: true });
  const FACES = [
    ["@fontsource-variable/dm-sans/standard.css", ["dm-sans-latin-ext-standard-normal", "dm-sans-latin-standard-normal"]],
    ["@fontsource/geist-mono/400.css", ["geist-mono-latin-ext-400-normal", "geist-mono-latin-400-normal"]],
    ["@fontsource/geist-mono/500.css", ["geist-mono-latin-ext-500-normal", "geist-mono-latin-500-normal"]],
  ];
  const faces = [];
  for (const [cssPath, wanted] of FACES) {
    const file = require.resolve(cssPath);
    const css = readFileSync(file, "utf8");
    for (const m of css.matchAll(/\/\* ([\w-]+) \*\/\s*@font-face\s*\{[^}]*\}/g)) {
      if (!wanted.includes(m[1])) continue;
      for (const woff2 of m[0].matchAll(/url\(\.\/files\/([^)]+\.woff2)\)/g)) {
        copyFileSync(path.join(path.dirname(file), "files", woff2[1]), path.join(fonts, woff2[1]));
      }
      faces.push(
        m[0]
          .replace(/url\(\.\/files\//g, "url(./")
          .replace(/, url\(\.\/[^)]+\.woff\) format\('woff'\)/g, ""),
      );
    }
    if (faces.length === 0) throw new Error(`no faces found in ${cssPath}; @fontsource changed shape`);
  }
  writeFileSync(
    path.join(fonts, "fonts.css"),
    `/* DM Sans Variable and Geist Mono, from the web client's @fontsource packages.\n   DM Sans for anything a person says or reads; Geist Mono for anything the\n   machine says about itself. */\n\n${faces.join("\n\n")}\n`,
  );
  writeFileSync(
    path.join(OUT, "styles.css"),
    `/* Alfred design system: the one stylesheet to link. Fonts, then the compiled\n   component styles, which carry every token (colour per theme, type, easing). */\n@import url("./fonts/fonts.css");\n@import url("./_ds_bundle.css");\n`,
  );
  console.log("assets ok", faces.length, "font faces");
}

if (!only || only === "vendor") {
  mkdirSync(path.join(OUT, "_vendor"), { recursive: true });
  mkdirSync(CACHE, { recursive: true });
  // A default import of a CommonJS module is its module.exports, whole. `export *`
  // cannot enumerate CommonJS names statically, and silently exports nothing.
  writeFileSync(path.join(CACHE, "vendor-react.js"), 'import R from "react";\nwindow.React = R;\n');
  writeFileSync(
    path.join(CACHE, "vendor-react-dom.js"),
    'import D from "react-dom";\nimport C from "react-dom/client";\nwindow.ReactDOM = Object.assign({}, D, C);\n',
  );
  await iife({ entry: path.join(CACHE, "vendor-react.js"), name: "__vendorReact", file: "react.js", outDir: path.join(OUT, "_vendor"), minify: true });
  await iife({
    entry: path.join(CACHE, "vendor-react-dom.js"),
    name: "__vendorReactDOM",
    file: "react-dom.js",
    outDir: path.join(OUT, "_vendor"),
    globals: ["react"],
    minify: true,
  });
  console.log("vendor ok");
}

if (!only || only === "lib") {
  await iife({
    entry: path.join(HERE, "entry.tsx"),
    name: "Alfred",
    file: "_ds_bundle.js",
    globals: ["react", "react-dom"],
    css: true,
  });
  const js = path.join(OUT, "_ds_bundle.js");
  const names = [...readFileSync(path.join(HERE, "entry.tsx"), "utf8").matchAll(/export \{([^}]+)\}/g)]
    .flatMap((m) => m[1].split(","))
    .map((s) => s.trim())
    .filter(Boolean);
  const header = `/* @ds-bundle: ${JSON.stringify({ name: "Alfred", global: "Alfred", version: "0.0.0", source: "anirudhlath/alfred web/src", exports: names })} */\n`;
  writeFileSync(js, header + readFileSync(js, "utf8"));
  if (!existsSync(path.join(OUT, "_ds_bundle.css"))) {
    const stray = readdirSync(OUT).find((f) => f.endsWith(".css") && f !== "_ds_bundle.css");
    if (stray) renameSync(path.join(OUT, stray), path.join(OUT, "_ds_bundle.css"));
  }
  console.log("lib ok", names.length, "exports");
}

if (!only || only === "previews") {
  mkdirSync(path.join(OUT, "_preview"), { recursive: true });
  const dir = path.join(HERE, "previews");
  const files = readdirSync(dir).filter((f) => f.endsWith(".tsx"));
  for (const f of files) {
    await iife({
      entry: path.join(dir, f),
      name: "__dsPreview",
      file: f.replace(/\.tsx$/, ".js"),
      outDir: path.join(OUT, "_preview"),
      globals: ["react", "react-dom", "@ds"],
    });
  }
  console.log("previews ok", files.length);
}
