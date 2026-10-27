// Opens every component card in Chromium (and every story of it, alone), and
// records what a person would see go wrong: page errors, console errors, failed
// requests, a story that rendered nothing, and stories that render identically.
// Writes ../render-check.json and screenshots to ../shots/.
import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { readFileSync, existsSync, mkdirSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { execSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Playwright is not a web/ dependency: the cloud dev container ships it globally.
// Use the local install if there is one, else the global one, else say what to do.
const require = createRequire(import.meta.url);
function loadPlaywright() {
  try {
    return require("playwright");
  } catch {
    try {
      return require(path.join(execSync("npm root -g").toString().trim(), "playwright"));
    } catch {
      console.error(
        "render check needs Playwright with Chromium: `npm i -g playwright && npx playwright install chromium`.\n" +
          "Set DESIGN_SYNC_SKIP_RENDER=1 to skip it, and do not upload what was not checked.",
      );
      process.exit(1);
    }
  }
}
const { chromium } = loadPlaywright();

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, "out");
const SHOTS = path.join(HERE, ".cache/shots");
mkdirSync(SHOTS, { recursive: true });

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2" };
const server = createServer((req, res) => {
  const file = path.join(OUT, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!file.startsWith(OUT) || !existsSync(file) || statSync(file).isDirectory()) {
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream" });
  res.end(readFileSync(file));
}).listen(0);
const base = `http://127.0.0.1:${server.address().port}`;

const cards = [];
for (const group of readdirSync(path.join(OUT, "components"))) {
  for (const name of readdirSync(path.join(OUT, "components", group))) cards.push({ group, name });
}

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
);
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
const results = [];
for (const { group, name } of cards) {
  const url = `${base}/components/${group}/${name}/${name}.html`;
  const page = await ctx.newPage();
  const problems = [];
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && problems.push(`console: ${m.text()}`));
  page.on("requestfailed", (r) => problems.push(`requestfailed: ${r.url()}`));
  page.on("response", (r) => r.status() >= 400 && !r.url().endsWith("/favicon.ico") && problems.push(`http ${r.status()}: ${r.url()}`));
  await page.goto(url, { waitUntil: "load" });
  await page.waitForTimeout(900);
  const stories = await page.evaluate(() => window.__dsStories || []);
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });

  const per = [];
  for (const story of stories) {
    const sp = await ctx.newPage();
    const sProblems = [];
    sp.on("pageerror", (e) => sProblems.push(`pageerror: ${e.message}`));
    sp.on("console", (m) => m.type() === "error" && sProblems.push(`console: ${m.text()}`));
    await sp.setViewportSize({ width: 430, height: 900 });
    await sp.goto(`${url}?story=${story}`, { waitUntil: "load" });
    await sp.waitForTimeout(900);
    const box = await sp.evaluate(() => {
      const r = document.getElementById("ds").getBoundingClientRect();
      const text = document.body.innerText.trim().length;
      const nodes = document.body.querySelectorAll("*").length;
      return { w: Math.round(r.width), h: Math.round(r.height), text, nodes };
    });
    const png = await sp.screenshot({ path: path.join(SHOTS, `${name}--${story}.png`) });
    per.push({ story, ...box, hash: createHash("sha1").update(png).digest("hex").slice(0, 12), problems: sProblems });
    await sp.close();
  }
  const hashes = per.map((p) => p.hash);
  results.push({
    group,
    name,
    stories: per,
    problems,
    thin: per.filter((p) => p.h < 12 || p.nodes < 3).map((p) => p.story),
    identical: hashes.length !== new Set(hashes).size,
  });
  await page.close();
}
await browser.close();
server.close();

const bad = results.filter((r) => r.problems.length || r.stories.some((s) => s.problems.length));
const summary = {
  total: results.length,
  bad: bad.length,
  thin: results.filter((r) => r.thin.length).length,
  variantsIdentical: results.filter((r) => r.identical).length,
};
writeFileSync(path.join(HERE, ".cache/render-check.json"), JSON.stringify({ summary, results }, null, 2));
console.log(JSON.stringify(summary));
if (summary.bad || summary.thin || summary.variantsIdentical) process.exitCode = 1;
for (const r of results) {
  const issues = [...r.problems, ...r.stories.flatMap((s) => s.problems.map((p) => `${s.story}: ${p}`))];
  if (issues.length || r.thin.length || r.identical)
    console.log(`- ${r.name}: ${[...new Set(issues)].slice(0, 4).join(" | ")}${r.thin.length ? ` thin=${r.thin}` : ""}${r.identical ? " identical-variants" : ""}`);
}
