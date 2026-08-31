/**
 * Verifies every internal link in the built site resolves to a real file, and
 * that every page has the SEO tags that make it indexable.
 *
 * Runs against dist/, so it checks what actually ships rather than the source.
 * External links (http/https/mailto) are reported but not fetched — the point
 * is to catch our own broken hrefs, not to flake on someone else's downtime.
 */
import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import path from "node:path";

const dist = path.resolve(import.meta.dirname, "../dist");
if (!existsSync(dist)) {
  console.error("dist/ not found — run `npm run build` first.");
  process.exit(1);
}

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : [full];
  });
}

const files = walk(dist);
const pages = files.filter((f) => f.endsWith(".html"));
const errors = [];
let checked = 0;

/** Does an internal href correspond to something in dist/? */
function resolves(href) {
  const clean = href.split("#")[0].split("?")[0];
  if (clean === "" || clean === "/") return existsSync(path.join(dist, "index.html"));
  const rel = clean.replace(/^\//, "");
  const candidates = [
    path.join(dist, rel),
    path.join(dist, `${rel}.html`),
    path.join(dist, rel, "index.html"),
  ];
  return candidates.some((c) => existsSync(c) && statSync(c).isFile());
}

for (const file of pages) {
  const html = readFileSync(file, "utf8");
  const page = "/" + path.relative(dist, file).replace(/\\/g, "/");

  // --- links ---
  for (const match of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
    const href = match[1];
    if (/^(https?:|mailto:|tel:|data:|#)/.test(href)) continue;
    checked++;
    if (!resolves(href)) errors.push(`${page}: broken link → ${href}`);
  }

  // --- SEO essentials ---
  const need = [
    [/<title>[^<]{5,}<\/title>/, "a non-empty <title>"],
    [/<meta name="description" content="[^"]{20,}"/, "a meta description"],
    [/<link rel="canonical" href="https?:\/\/[^"]+"/, "a canonical URL"],
    [/<meta property="og:image" content="https?:\/\/[^"]+"/, "an og:image"],
    [/<html lang="[a-z]{2}"/, "a lang attribute"],
  ];
  for (const [re, what] of need) {
    if (!re.test(html)) errors.push(`${page}: missing ${what}`);
  }

  // A page with more than one <h1> is an outline bug and an a11y problem.
  const h1s = (html.match(/<h1[\s>]/g) || []).length;
  if (h1s !== 1) errors.push(`${page}: expected exactly one <h1>, found ${h1s}`);
}

// The files that must exist for search engines and app deep links.
for (const required of [
  "sitemap-index.xml",
  "robots.txt",
  "rss.xml",
  "og-default.png",
  "404.html",
  "_headers",
  ".well-known/apple-app-site-association",
  ".well-known/assetlinks.json",
]) {
  if (!existsSync(path.join(dist, required))) errors.push(`missing required file: ${required}`);
}

console.log(`checked ${checked} internal links across ${pages.length} pages`);
if (errors.length) {
  console.error(`\n${errors.length} problem(s):`);
  for (const e of errors) console.error(`  ✗ ${e}`);
  process.exit(1);
}
console.log("✓ all internal links resolve and every page has its SEO tags");
