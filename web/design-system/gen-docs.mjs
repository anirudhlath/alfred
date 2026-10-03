// Writes everything in ../out that is not a build product, from the code:
//   components/<group>/<Name>/<Name>.{jsx,d.ts,prompt.md,html}
//   README.md, guidelines/*.md
// Props and descriptions come from the TypeScript declarations tsc emits for
// web/src (JSDoc intact); tokens, type and motion from web/src/index.css; the
// examples from the preview stories. Nothing is restated by hand, so a re-run
// after a code change is a re-sync.
import { mkdirSync, readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, "out");
const DECL = path.join(HERE, ".cache/decl");
const WEB = path.resolve(HERE, "..");
const WEB_SRC = path.join(WEB, "src");
const INDEX_CSS = path.join(WEB_SRC, "index.css");

const GROUPS = {
  foundation: ["Page", "RoomScreen"],
  room: [
    "Headline",
    "StatusLine",
    "OfflineNote",
    "DndRow",
    "Composer",
    "HoldToTalk",
    "WorkshopHandle",
    "PresenceField",
    "ThemeToggle",
    "Timeline",
  ],
  timeline: [
    "YouBubble",
    "AlfredRow",
    "ActRow",
    "Divider",
    "Tombstone",
    "ThinkingRow",
    "TranscribingBubble",
    "FirstDay",
  ],
  door: ["FuseRing", "DoorBanner", "SlideToConfirm", "DoorLayer"],
  gates: ["Gate", "StepList", "DeniedGate"],
  sheets: ["Sheet"],
  workshop: [
    "BenchSwitcher",
    "StreamChips",
    "EventRow",
    "Switch",
    "SystemSection",
    "SystemRow",
    "RoutineRow",
    "TriggerRow",
  ],
};
/** Overlays portal to <body>: each story gets its own frame so they do not stack. */
const FRAMES = new Set(["DoorLayer", "Sheet"]);
/** Phone-sized stories need a column at least as wide as the phone. */
const PHONE = new Set(["RoomScreen", "Gate", "DeniedGate"]);

// ---------------------------------------------------------------- sources
const entry = readFileSync(path.join(HERE, "entry.tsx"), "utf8");
const moduleOf = {};
for (const m of entry.matchAll(/export \{([^}]+)\} from "([^"]+)"/g)) {
  for (const n of m[1].split(",").map((s) => s.trim()).filter(Boolean)) moduleOf[n] = m[2];
}

function declPath(spec, fromDir) {
  let abs;
  if (spec.startsWith("@/")) abs = path.join(WEB_SRC, spec.slice(2));
  else if (spec.startsWith(".")) abs = path.resolve(fromDir, spec);
  else return null;
  const p = path.join(DECL, `${path.relative(WEB, abs)}.d.ts`);
  return existsSync(p) ? p : null;
}
function sourceLabel(spec) {
  if (spec.startsWith("@/")) return `web/src/${spec.slice(2)}.tsx`;
  return `design-system canvas (${spec.replace(/^\.\//, "")}.tsx), not part of web/src`;
}
const srcDirOf = (decl) => path.join(WEB, path.dirname(path.relative(DECL, decl)));

/** One declaration by name from a .d.ts, with the JSDoc above it. */
function extract(text, name) {
  const re = new RegExp(
    `(?:/\\*\\*(?:(?!\\*/)[\\s\\S])*\\*/\\s*)?export (?:declare )?(?:interface|type|const|function|class|enum) ${name}\\b`,
  );
  const m = re.exec(text);
  if (!m) return null;
  let i = m.index + m[0].length;
  let depth = 0;
  for (; i < text.length; i++) {
    const c = text[i];
    if (c === ">" && text[i - 1] === "=") continue; // an arrow, not a closing generic
    if ("{([<".includes(c)) depth++;
    else if ("})]>".includes(c)) depth--;
    else if (c === ";" && depth === 0) return text.slice(m.index, i + 1);
    else if (c === "\n" && depth === 0 && text[i - 1] === "}") return text.slice(m.index, i);
  }
  return text.slice(m.index);
}

/** Names a .d.ts imports from sibling modules, with where to find each. */
function localImports(text, decl) {
  const out = [];
  for (const m of text.matchAll(/import (?:type )?\{([^}]+)\} from "([^"]+)";/g)) {
    const file = declPath(m[2], srcDirOf(decl));
    if (!file) continue;
    for (let n of m[1].split(",")) {
      n = n.trim().replace(/^type /, "").split(" as ")[0].trim();
      if (n) out.push([n, file]);
    }
  }
  for (const m of text.matchAll(/import\("([^"]+)"\)\.(\w+)/g)) {
    const file = declPath(m[1], srcDirOf(decl));
    if (file) out.push([m[2], file]);
  }
  return out;
}

/** The component's module declarations, then every local type they reach, inlined. */
function selfContainedDts(name) {
  const spec = moduleOf[name];
  const decl = declPath(spec, HERE);
  if (!decl) throw new Error(`no declarations for ${name} (${spec})`);
  const own = readFileSync(decl, "utf8");
  const seen = new Set();
  const extra = [];
  const queue = localImports(own, decl);
  while (queue.length) {
    const [n, file] = queue.shift();
    const key = `${file}#${n}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const body = extract(readFileSync(file, "utf8"), n);
    if (!body) continue;
    extra.push(body.replace(/^export declare /, "export "));
    queue.push(...localImports(body, file));
    // Names the body mentions: declared beside it, or imported into its file.
    const fileText = readFileSync(file, "utf8");
    const imported = Object.fromEntries(localImports(fileText, file));
    for (const m of body.matchAll(/\b([A-Z]\w+)\b/g)) {
      const where = extract(fileText, m[1]) ? file : imported[m[1]];
      if (where && !seen.has(`${where}#${m[1]}`)) queue.push([m[1], where]);
    }
  }
  const body = own.replace(/^import (?:type )?\{[^}]+\} from "(?:@\/|\.)[^"]+";\n/gm, "");
  return [
    `// ${name}: from ${sourceLabel(spec)}. Generated by tsc from the source; do not edit.`,
    `// At runtime: window.Alfred.${name} (load styles.css and _ds_bundle.js).`,
    body.replace(/import\("(?:@\/|\.)[^"]+"\)\./g, "").trim(),
    extra.length ? `\n// ── Types it uses, from the same source tree ──\n\n${extra.join("\n\n")}` : "",
  ].join("\n");
}

/** The JSDoc block directly above `export declare function Name`. */
function docOf(name) {
  const decl = declPath(moduleOf[name], HERE);
  const text = readFileSync(decl, "utf8");
  const re = new RegExp(`(/\\*\\*(?:(?!\\*/)[\\s\\S])*\\*/)\\s*export declare function ${name}\\b`);
  const m = re.exec(text);
  if (!m) return "";
  return m[1]
    .replace(/^\/\*\*\s*/, "")
    .replace(/\s*\*\/$/, "")
    .split("\n")
    .map((l) => l.replace(/^\s*\* ?/, ""))
    .join("\n")
    .trim();
}
const firstSentence = (doc) => {
  const flat = doc.replace(/\s+/g, " ").trim();
  const m = /^(.+?[.!?])(\s|$)/.exec(flat);
  return m ? m[1] : flat;
};

function propsOf(name) {
  const decl = declPath(moduleOf[name], HERE);
  const text = readFileSync(decl, "utf8");
  const props = extract(text, `${name}Props`);
  if (props) return props;
  const fn = new RegExp(`export declare function ${name}\\(([\\s\\S]*?)\\): import`).exec(text);
  return fn ? `// ${name}'s props, inline in its signature\n(${fn[1].trim()})` : "";
}

function storyExample(name) {
  const src = readFileSync(path.join(HERE, "previews", `${name}.tsx`), "utf8");
  return src.replace(/^import[^\n]*\n/gm, "").trim();
}
const storyNames = (name) =>
  [...readFileSync(path.join(HERE, "previews", `${name}.tsx`), "utf8").matchAll(/export const ([A-Z]\w*)/g)].map(
    (m) => m[1],
  );

// ---------------------------------------------------------------- card
function card(name, group) {
  const mode = FRAMES.has(name) ? "frames" : "grid";
  const min = FRAMES.has(name) || PHONE.has(name) ? 393 : 320;
  return `<!-- @dsCard group="${group}" -->
<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${name} · Alfred</title>
<link rel="stylesheet" href="../../../styles.css">
<style>
  html, body { margin: 0; }
  body.ds-card { padding: 20px; background: #E9E6E1; font: 12px/1.4 system-ui, sans-serif; }
  .ds-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(${min}px, 1fr)); gap: 16px; align-items: start; }
  .ds-cell { min-width: 0; }
  .ds-cell > h4 { margin: 0 0 6px; font: 500 11px/1 ui-monospace, monospace; letter-spacing: .06em; text-transform: uppercase; color: #6B645C; }
  .ds-frame { display: block; width: 393px; height: 852px; border: 0; }
  .ds-error { font: 12px/1.5 ui-monospace, monospace; color: #8A3B12; white-space: pre-wrap; }
</style>
</head><body class="ds-card">
<div id="ds" class="ds-grid"></div>
<script src="../../../_vendor/react.js"></script>
<script src="../../../_vendor/react-dom.js"></script>
<script src="../../../_ds_bundle.js"></script>
<script src="../../../_preview/${name}.js"></script>
<script>
(function () {
  var MODE = "${mode}";
  var P = window.__dsPreview || {};
  var ORDER = ${JSON.stringify(storyNames(name))};
  var stories = ORDER.filter(function (k) { return typeof P[k] === "function"; });
  var root = document.getElementById("ds");
  window.__dsStories = stories.slice();
  function mount(el, key) {
    try { ReactDOM.createRoot(el).render(React.createElement(P[key])); }
    catch (e) { el.className = "ds-error"; el.textContent = String(e && e.message || e); }
  }
  var q = null;
  try { q = new URLSearchParams(location.search).get("story"); } catch (e) {}
  if (q) {
    // One story, full bleed. A layer portals to <body>, outside its Page, so the
    // document takes the Page's theme the way the app's <html> carries it.
    document.body.className = ""; root.className = "";
    var key = stories.filter(function (k) { return k.toLowerCase() === q.toLowerCase(); })[0];
    if (!key) { root.className = "ds-error"; root.textContent = "no story named " + q; return; }
    var sync = function () {
      var page = root.querySelector("[data-theme]");
      if (page) document.documentElement.setAttribute("data-theme", page.getAttribute("data-theme"));
    };
    new MutationObserver(sync).observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-theme"] });
    mount(root, key);
    return;
  }
  stories.forEach(function (key) {
    var cell = document.createElement("section");
    cell.className = "ds-cell";
    var label = document.createElement("h4");
    label.textContent = key;
    cell.appendChild(label);
    if (MODE === "frames") {
      var frame = document.createElement("iframe");
      frame.className = "ds-frame"; frame.title = key; frame.src = "?story=" + encodeURIComponent(key);
      cell.appendChild(frame);
    } else {
      var slot = document.createElement("div");
      cell.appendChild(slot);
      mount(slot, key);
    }
    root.appendChild(cell);
  });
  if (!stories.length) { root.className = "ds-error"; root.textContent = "no stories in _preview/${name}.js"; }
})();
</script>
</body></html>
`;
}

// ---------------------------------------------------------------- index.css
const css = readFileSync(INDEX_CSS, "utf8");
const cleanComment = (c) =>
  c
    .replace(/^\/\*+\s?/, "")
    .replace(/\s*\*\/$/, "")
    .split("\n")
    .map((l) => l.replace(/^\s{0,6}/, ""))
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();

function block(selectorStart) {
  const i = css.indexOf(selectorStart);
  if (i < 0) throw new Error(`index.css: no ${selectorStart}`);
  const open = css.indexOf("{", i);
  let depth = 0;
  for (let j = open; j < css.length; j++) {
    if (css[j] === "{") depth++;
    else if (css[j] === "}" && --depth === 0) return css.slice(open + 1, j);
  }
  throw new Error("unbalanced");
}
function tokens(body) {
  const out = [];
  let note = "";
  for (const m of body.matchAll(/(\/\*[\s\S]*?\*\/)|(--[\w-]+)\s*:\s*([^;]+);/g)) {
    if (m[1]) note = cleanComment(m[1]);
    else {
      out.push({ name: m[2], value: m[3].trim(), note });
      note = "";
    }
  }
  return out;
}
const dark = tokens(block(':root[data-theme="dark"]'));
const light = tokens(block(':root[data-theme="light"] {'));
const shared = tokens(block(":root {\n  --ease-rise"));
const lightOf = Object.fromEntries(light.map((t) => [t.name, t]));

const typeClasses = [];
for (const m of css.matchAll(/((?:\/\*[\s\S]*?\*\/\s*)?)\.(t-[\w-]+)\s*\{([^}]*)\}/g)) {
  const props = m[3]
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
    .join("; ");
  typeClasses.push({ name: m[2], props, note: m[1] ? cleanComment(m[1].trim()) : "" });
}
const keyframes = [...css.matchAll(/@keyframes ([\w-]+)/g)].map((m) => m[1]);
const sectionNote = (heading) => {
  const m = new RegExp(`/\\* -+ ?${heading}[\\s\\S]*?\\*/`).exec(css);
  return m ? cleanComment(m[0]).replace(/^-+\s*/, "") : "";
};

// ---------------------------------------------------------------- write
rmSync(path.join(OUT, "components"), { recursive: true, force: true });
rmSync(path.join(OUT, "guidelines"), { recursive: true, force: true });
const index = [];
for (const [group, names] of Object.entries(GROUPS)) {
  for (const name of names) {
    if (!moduleOf[name]) throw new Error(`${name} is not exported from entry.tsx`);
    const dir = path.join(OUT, "components", group, name);
    mkdirSync(dir, { recursive: true });
    const doc = docOf(name);
    index.push({ group, name, summary: firstSentence(doc) || "(no doc comment in source)" });
    writeFileSync(
      path.join(dir, `${name}.jsx`),
      `// ${name} from the Alfred design system (${sourceLabel(moduleOf[name])}).\n// The implementation is in the root _ds_bundle.js, as window.Alfred.${name}.\nObject.assign(window, { ${name}: window.Alfred.${name} });\n`,
    );
    writeFileSync(path.join(dir, `${name}.d.ts`), `${selfContainedDts(name)}\n`);
    writeFileSync(
      path.join(dir, `${name}.prompt.md`),
      [
        `# ${name}`,
        "",
        `\`window.Alfred.${name}\` · source: \`${sourceLabel(moduleOf[name])}\``,
        "",
        doc || "_The source has no doc comment for this component._",
        "",
        `## Stories (${storyNames(name).join(", ")})`,
        "",
        "Each story is a real render of the component. Everything goes inside a `<Page>`, which supplies the theme.",
        "",
        "```jsx",
        storyExample(name),
        "```",
        "",
        "## Props",
        "",
        "```ts",
        propsOf(name),
        "```",
        "",
        `Full types, including the data shapes the props take: \`${name}.d.ts\`.`,
        "",
      ].join("\n"),
    );
    writeFileSync(path.join(dir, `${name}.html`), card(name, group));
  }
}

// guidelines
mkdirSync(path.join(OUT, "guidelines"), { recursive: true });
// Markdown table cells: escape backslashes first, then the pipes that would split a cell.
const esc = (s) => s.replace(/\\/g, "\\\\").replace(/\|/g, "\\|");
writeFileSync(
  path.join(OUT, "guidelines", "colour.md"),
  [
    "# Colour tokens",
    "",
    "Generated from `web/src/index.css`. Each token has a value per theme; the note is the stylesheet's own comment on it. Use the token, never the value.",
    "",
    "| token | dark | light | note |",
    "|---|---|---|---|",
    ...dark.map(
      (t) =>
        `| \`${t.name}\` | \`${esc(t.value)}\` | \`${esc(lightOf[t.name]?.value ?? "—")}\` | ${esc([t.note, lightOf[t.name]?.note].filter(Boolean).join(" Light: "))} |`,
    ),
    "",
    "Stream hues are data, not tokens: `ring(hue)`, `ringFill(hue)` and `ringText(hue)` (exported on `window.Alfred`) turn a stream's hue from `STREAM_INFO` into a colour.",
    "",
    "| stream | mono | hue |",
    "|---|---|---|",
    ...[...readFileSync(path.join(WEB_SRC, "lib/streams.ts"), "utf8").matchAll(/(\w+): \{ mono: "(\w+)", hue: (\d+) \}/g)].map(
      (m) => `| ${m[1]} | ${m[2]} | ${m[3]} |`,
    ),
    "",
  ].join("\n"),
);
writeFileSync(
  path.join(OUT, "guidelines", "type.md"),
  [
    "# Type",
    "",
    `Generated from \`web/src/index.css\`. ${sectionNote("the type scale")}`,
    "",
    "Fonts: `--font-sans` " +
      `\`${shared.find((t) => t.name === "--font-sans")?.value}\`` +
      ", `--font-mono` " +
      `\`${shared.find((t) => t.name === "--font-mono")?.value}\`.`,
    "",
    "| class | declarations | note |",
    "|---|---|---|",
    ...typeClasses.map((t) => `| \`.${t.name}\` | \`${esc(t.props)}\` | ${esc(t.note)} |`),
    "",
  ].join("\n"),
);
writeFileSync(
  path.join(OUT, "guidelines", "motion.md"),
  [
    "# Motion",
    "",
    `Generated from \`web/src/index.css\`. ${sectionNote("motion")}`,
    "",
    "| token | value | note |",
    "|---|---|---|",
    ...shared.filter((t) => t.name.startsWith("--ease")).map((t) => `| \`${t.name}\` | \`${t.value}\` | ${esc(t.note)} |`),
    "",
    `Classes: \`.rise-in\` / \`.rise-out\` (duration from \`--layer-duration\`, set by the component). Keyframes: ${keyframes.map((k) => `\`${k}\``).join(", ")}.`,
    "",
    "Reduced motion: every animation and transition is cut to 0.01 ms, and `rise`/`sink` become a 200 ms opacity step (`fade`/`fade-out`).",
    "",
  ].join("\n"),
);
writeFileSync(
  path.join(OUT, "guidelines", "index.md"),
  [
    "# Guidelines",
    "",
    "All generated from the implemented web client, so they describe what ships, not what was intended.",
    "",
    "- `colour.md`: every colour token in both themes, with the stylesheet's notes on contrast and use.",
    "- `type.md`: the type-scale classes and when each applies.",
    "- `motion.md`: the easing curves, enter/leave classes and keyframes.",
    "",
    "For a component's own rules, read its `.prompt.md`: the text there is the doc comment from its source file.",
    "",
  ].join("\n"),
);

// README
const byGroup = Object.entries(GROUPS).map(([g]) => {
  const rows = index.filter((r) => r.group === g);
  return [`### ${g}`, "", ...rows.map((r) => `- \`${r.name}\`: ${r.summary}`), ""].join("\n");
});
writeFileSync(
  path.join(OUT, "README.md"),
  `# Alfred

The design system of Alfred's web client (\`anirudhlath/alfred\`, \`web/\`), a phone-first PWA for a self-hosted household assistant: **the Room** (the conversation, with Alfred's own acts in the same thread), **the Door** (a full-screen interrupt to approve a critical action, confirmed by a slide), **the Workshop** (four benches: Activity, Memory, Triggers, System) and **the gates** (sign-in, first run, 401/403).

It is generated from the implemented code, and kept in sync by re-running the generator, never by editing here:

- Components are the client's own React components from \`web/src\`, bundled unmodified.
- Props come from the TypeScript declarations of those components.
- Descriptions are the components' doc comments.
- Tokens, type and motion come from \`web/src/index.css\`.

Two pieces exist only for the canvas: \`Page\`, which gives a subtree a theme (the app puts it on \`<html>\`), and \`RoomScreen\`, the Room assembled from its real parts with props in place of the socket.

## Building with it

- **Everything goes inside a \`<Page>\`.** \`theme="dark"\` (the default, and the app's first paint) or \`"light"\`. \`frame="phone"\` gives the 393 × 852 shell the client is drawn at; the default \`frame="fill"\` is a padded block for laying out parts.
- **Start a screen from \`RoomScreen\`, \`DoorLayer\`, \`Gate\` or \`Sheet\`.** Those are the full surfaces. Rows and controls compose inside them.
- **Tokens, never literals.** Colour is \`var(--bg)\`, \`var(--fg)\`, \`var(--accent-text)\` and the rest in \`guidelines/colour.md\`; text uses the \`.t-*\` classes in \`guidelines/type.md\`.
- **Read the stylesheet's notes before choosing a colour.** Several tokens exist only to keep contrast at AA in the light theme: \`--accent-text\` for accent used as text (never \`--accent\`), \`--green-text\` likewise, \`--on-accent\` for anything drawn on an accent fill, \`.t-meta-strong\` where muted text is the only words in its region.
- **The Door inverts.** Its surfaces use \`--ink\` as the background and \`--paper\` / \`--paper-muted\` as text, in both themes.

\`\`\`jsx
const { Page, RoomScreen, AlfredRow, Divider } = window.Alfred;

<Page theme="dark" frame="phone">
  <RoomScreen headline="Listening, sir." items={[]} firstDayGreeting="Good morning, sir." />
</Page>
\`\`\`

## Loading

\`\`\`html
<link rel="stylesheet" href="styles.css">
<script src="_vendor/react.js"></script>
<script src="_vendor/react-dom.js"></script>
<script src="_ds_bundle.js"></script>
\`\`\`

React 19 ships no browser build, so \`_vendor/\` holds one (\`window.React\`, \`window.ReactDOM\`). Components are on \`window.Alfred\`. Mount into your own node: \`ReactDOM.createRoot(el).render(...)\`. \`Sheet\` and \`DoorLayer\` portal to \`<body>\` and cover the viewport, as in the app; give \`<html>\` the same \`data-theme\` as the Page around them.

## Where things are

- \`_ds_bundle.js\`, \`_ds_bundle.css\`: the system. \`styles.css\` is the one stylesheet to link (fonts, then the bundle CSS, which holds every token).
- \`components/<group>/<Name>/\`: \`.prompt.md\` (doc comment, live example, props), \`.d.ts\` (full types, including the data shapes), \`.html\` (every story rendered).
- \`guidelines/\`: colour, type and motion, generated from the stylesheet.
- \`fonts/\`: DM Sans (variable) and Geist Mono, self-hosted.

## Components

${byGroup.join("\n")}`,
);

console.log(`docs ok: ${index.length} components`);
