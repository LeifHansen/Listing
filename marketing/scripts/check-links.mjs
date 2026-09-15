/**
 * Verifies every internal link in the built site resolves to a real file, and
 * that every page has the SEO tags that make it indexable.
 *
 * Runs against dist/, so it checks what actually ships rather than the source.
 * External links (http/https/mailto) are reported but not fetched — the point
 * is to catch our own broken hrefs, not to flake on someone else's downtime.
 *
 * The SEO half of this file exists because none of what it checks fails loudly.
 * A page that loses its canonical, a JSON-LD graph whose @id references stopped
 * resolving after a route was renamed, two pages that quietly ended up with the
 * same meta description — all of them build, deploy and serve a 200. The only
 * symptom is traffic that never arrives, months later. So the invariants are
 * asserted here, where getting one wrong fails a pull request instead.
 */
import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import path from "node:path";
import { APP_URL } from "../src/lib/site.js";

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
let appLinks = 0;

// Collected across pages so duplicates can be reported once, at the end. Two
// pages sharing a title or a description is how a site ends up competing with
// itself for a query and gets one of the two dropped as a near-duplicate.
const titles = new Map();
const descriptions = new Map();
let ldNodes = 0;

/**
 * The origin this build was made for, read from the home page's own canonical
 * rather than from SITE_URL — the point is to check what shipped, not what the
 * environment claimed at build time.
 */
const origin = (() => {
  const home = path.join(dist, "index.html");
  if (!existsSync(home)) {
    console.error("dist/index.html not found — the build produced no home page.");
    process.exit(1);
  }
  const canonical = readFileSync(home, "utf8").match(/<link rel="canonical" href="([^"]+)"/)?.[1];
  if (!canonical) {
    console.error("dist/index.html has no canonical — every other check depends on it.");
    process.exit(1);
  }
  return new URL(canonical).origin + "/";
})();

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

  // --- links into the app open in a new tab ---
  // A CTA that navigates the current tab leaves this page one Back press behind
  // the app, so Back — or a phone's edge swipe — throws a seller out of the
  // product they just signed in to. `appLink()` in src/lib/site.js is the one
  // place that sets the attributes; this is what makes forgetting it fail, for
  // a hand-written href as much as a missed helper.
  for (const [tag] of html.matchAll(/<a\b[^>]*>/g)) {
    const href = tag.match(/href="([^"]*)"/)?.[1];
    if (!href?.startsWith(APP_URL)) continue;
    appLinks++;
    if (!/\btarget="_blank"/.test(tag)) {
      errors.push(`${page}: app link without target="_blank" → ${href}`);
    }
    if (!/\brel="[^"]*\bnoopener\b[^"]*"/.test(tag)) {
      errors.push(`${page}: app link without rel="noopener" → ${href}`);
    }
  }

  // --- SEO essentials ---
  const need = [
    [/<title>[^<]{5,}<\/title>/, "a non-empty <title>"],
    [/<meta name="description" content="[^"]{20,}"/, "a meta description"],
    [/<link rel="canonical" href="https?:\/\/[^"]+"/, "a canonical URL"],
    [/<meta name="robots" content="[^"]+"/, "a robots directive"],
    [/<meta property="og:image" content="https?:\/\/[^"]+"/, "an og:image"],
    [/<meta property="og:image:alt" content="[^"]+"/, "an og:image:alt"],
    [/<meta property="og:locale" content="[a-z]{2}_[A-Z]{2}"/, "an og:locale"],
    [/<meta name="twitter:card" content="summary_large_image"/, "a twitter card"],
    [/<script type="application\/ld\+json">/, "a JSON-LD block"],
    [/<html lang="[a-z]{2}"/, "a lang attribute"],
  ];
  for (const [re, what] of need) {
    if (!re.test(html)) errors.push(`${page}: missing ${what}`);
  }

  const noindex = /<meta name="robots" content="noindex/.test(html);

  // An indexable page must actually ASK to be indexed, and ask for the large
  // image preview — without it Google picks a small thumbnail and the page is
  // ineligible for Discover, whatever the og:image says.
  if (!noindex && !/<meta name="robots" content="index, follow, max-image-preview:large/.test(html)) {
    errors.push(`${page}: indexable page without the max-image-preview directive`);
  }

  // --- the strings that appear in a search result ---
  const title = html.match(/<title>([^<]*)<\/title>/)?.[1] ?? "";
  const description = html.match(/<meta name="description" content="([^"]*)"/)?.[1] ?? "";

  if (!noindex) {
    // Google renders roughly 60 characters of a title and 155 of a snippet
    // before truncating. A little over is fine — the tail is usually the brand
    // suffix — but a title that runs long has had its distinguishing words cut
    // off, and a description under ~70 characters has left the space empty.
    if (title.length > 70) errors.push(`${page}: title is ${title.length} chars, over the 70 that survive truncation`);
    if (description.length < 70) errors.push(`${page}: meta description is only ${description.length} chars — too thin to earn the click`);
    if (description.length > 165) errors.push(`${page}: meta description is ${description.length} chars and will be cut off`);

    if (titles.has(title)) errors.push(`${page}: duplicate <title>, same as ${titles.get(title)}`);
    else titles.set(title, page);
    if (descriptions.has(description)) errors.push(`${page}: duplicate meta description, same as ${descriptions.get(description)}`);
    else descriptions.set(description, page);
  }

  // A page with more than one <h1> is an outline bug and an a11y problem.
  const h1s = (html.match(/<h1[\s>]/g) || []).length;
  if (h1s !== 1) errors.push(`${page}: expected exactly one <h1>, found ${h1s}`);

  // The canonical, og:url and sitemap must all name the SAME url. The build
  // emits .html files that the host serves at clean paths, so it is easy for
  // the canonical to advertise /pricing.html while the sitemap says /pricing —
  // which tells a crawler the two are different pages. Pin the one true form:
  // no .html, and no trailing slash except on the root.
  const canonical = html.match(/<link rel="canonical" href="([^"]+)"/)?.[1];
  if (canonical) {
    if (canonical.endsWith(".html")) {
      errors.push(`${page}: canonical carries a .html suffix → ${canonical}`);
    }
    const afterOrigin = canonical.replace(/^https?:\/\/[^/]+/, "");
    if (afterOrigin.length > 1 && afterOrigin.endsWith("/")) {
      errors.push(`${page}: canonical has a trailing slash → ${canonical}`);
    }
    const ogUrl = html.match(/<meta property="og:url" content="([^"]+)"/)?.[1];
    if (ogUrl && ogUrl !== canonical) {
      errors.push(`${page}: og:url (${ogUrl}) disagrees with canonical (${canonical})`);
    }
  }

  // --- structured data ---
  //
  // One graph per page, not several blocks. Separate blocks describe things a
  // parser has no reason to connect; the @id references inside one graph are
  // what tie an article to the organization that published it. Those
  // references are also the part that rots silently — rename a route and a
  // `isPartOf` still parses as valid JSON while pointing at nothing — so every
  // one of them is resolved here, against this page's own nodes first and then
  // against the built site.
  const ldBlocks = [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)];
  if (ldBlocks.length > 1) {
    errors.push(`${page}: ${ldBlocks.length} JSON-LD blocks — they belong in one @graph`);
  }
  for (const [, raw] of ldBlocks) {
    let graph;
    try {
      graph = JSON.parse(raw);
    } catch (e) {
      errors.push(`${page}: JSON-LD does not parse — ${e.message}`);
      continue;
    }
    if (!graph["@context"]) errors.push(`${page}: JSON-LD has no @context`);
    const nodes = graph["@graph"];
    if (!Array.isArray(nodes)) {
      errors.push(`${page}: JSON-LD is not a @graph`);
      continue;
    }
    ldNodes += nodes.length;

    for (const node of nodes) {
      if (!node["@type"]) errors.push(`${page}: a JSON-LD node has no @type`);
    }

    // Split every `@id` in the graph into definitions (an object that says
    // something about the thing) and bare references (an object that is only a
    // pointer). A reference must resolve: to a definition in this graph, or —
    // for a deliberate cross-page link such as a post's `isPartOf` the blog —
    // to a URL this site actually serves. That second case is the one that
    // rots: rename a route and the JSON stays valid while pointing at nothing.
    const defined = new Set();
    const references = [];
    (function collect(value) {
      if (Array.isArray(value)) return value.forEach(collect);
      if (!value || typeof value !== "object") return;
      const keys = Object.keys(value);
      if (keys.length === 1 && keys[0] === "@id") {
        references.push(value["@id"]);
        return;
      }
      if (value["@id"]) defined.add(value["@id"]);
      keys.forEach((k) => collect(value[k]));
    })(nodes);

    for (const ref of references) {
      if (defined.has(ref)) continue;
      const target = ref.split("#")[0];
      if (target.startsWith(origin) && resolves(target.slice(origin.length - 1))) continue;
      errors.push(`${page}: JSON-LD @id reference resolves to nothing → ${ref}`);
    }

    if (!canonical) continue;

    // The page's own nodes must name the page. A WebPage whose url is not the
    // canonical is describing a different URL than the one being served.
    const webpage = nodes.find((n) => n["@id"] === `${canonical}#webpage`);
    if (!webpage) errors.push(`${page}: JSON-LD has no WebPage node for ${canonical}`);
    else if (webpage.url !== canonical) {
      errors.push(`${page}: WebPage url (${webpage.url}) disagrees with canonical (${canonical})`);
    }

    // Every page below the root carries a breadcrumb trail, and the trail ends
    // at the page itself — a BreadcrumbList whose last item points elsewhere is
    // worse than none, because it is what a result gets rendered from.
    const crumbs = nodes.find((n) => n["@type"] === "BreadcrumbList");
    const isRoot = canonical.replace(/\/$/, "") === origin.replace(/\/$/, "");
    if (!noindex && !isRoot) {
      if (!crumbs) errors.push(`${page}: no BreadcrumbList`);
      else {
        const last = crumbs.itemListElement.at(-1);
        if (last?.item !== canonical) {
          errors.push(`${page}: breadcrumb ends at ${last?.item}, not at ${canonical}`);
        }
        const positions = crumbs.itemListElement.map((c) => c.position);
        if (positions.some((pos, i) => pos !== i + 1)) {
          errors.push(`${page}: breadcrumb positions are not 1..n → ${positions.join(",")}`);
        }
      }
    }
  }
}

// Every canonical must appear in the sitemap, and vice versa — the two
// disagreeing is the failure this whole block exists to catch.
const sitemapPath = path.join(dist, "sitemap-0.xml");
if (existsSync(sitemapPath)) {
  const sitemap = readFileSync(sitemapPath, "utf8");
  const listed = new Set([...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]));
  for (const file of pages) {
    const html = readFileSync(file, "utf8");
    // 404 is deliberately noindex and is not a sitemap entry.
    if (/<meta name="robots" content="noindex/.test(html)) continue;
    const canonical = html.match(/<link rel="canonical" href="([^"]+)"/)?.[1];
    if (!canonical) continue;
    if (!listed.has(canonical) && !listed.has(canonical + "/")) {
      errors.push(
        `/${path.relative(dist, file)}: canonical ${canonical} is not in the sitemap`,
      );
    }
  }
}

// A sitemap's `lastmod` is the one field crawlers still act on, which makes a
// malformed or invented one actively harmful: a date in the future, or one that
// is not a date at all, is how a sitemap stops being believed.
if (existsSync(sitemapPath)) {
  const sitemap = readFileSync(sitemapPath, "utf8");
  const now = Date.now();
  for (const [, entry] of sitemap.matchAll(/<url>([\s\S]*?)<\/url>/g)) {
    const loc = entry.match(/<loc>([^<]+)<\/loc>/)?.[1];
    const lastmod = entry.match(/<lastmod>([^<]+)<\/lastmod>/)?.[1];
    const priority = entry.match(/<priority>([^<]+)<\/priority>/)?.[1];
    if (loc && !loc.startsWith(origin)) {
      errors.push(`sitemap: ${loc} is not on ${origin}`);
    }
    if (lastmod) {
      const when = Date.parse(lastmod);
      if (Number.isNaN(when)) errors.push(`sitemap: ${loc} has an unparseable lastmod → ${lastmod}`);
      else if (when > now) errors.push(`sitemap: ${loc} has a lastmod in the future → ${lastmod}`);
    }
    if (priority && !(Number(priority) >= 0 && Number(priority) <= 1)) {
      errors.push(`sitemap: ${loc} has a priority outside 0..1 → ${priority}`);
    }
  }
}

// The files that must exist for search engines and app deep links. Response
// headers are nginx's job now (security-headers.conf) and are checked by the
// container smoke test in .github/workflows/marketing.yml, not from dist/.
for (const required of [
  "sitemap-index.xml",
  "robots.txt",
  "rss.xml",
  "llms.txt",
  "og-default.png",
  "404.html",
  ".well-known/apple-app-site-association",
  ".well-known/assetlinks.json",
]) {
  if (!existsSync(path.join(dist, required))) errors.push(`missing required file: ${required}`);
}

// robots.txt naming a sitemap URL that does not exist is a silent no-op: the
// crawler fetches it, gets a 404, and falls back to discovering the site by
// crawling. Nothing anywhere reports that it happened.
const robotsPath = path.join(dist, "robots.txt");
if (existsSync(robotsPath)) {
  const robots = readFileSync(robotsPath, "utf8");
  const declared = robots.match(/^Sitemap:\s*(\S+)/m)?.[1];
  if (!declared) errors.push("robots.txt does not declare a Sitemap");
  else if (!declared.startsWith(origin)) {
    errors.push(`robots.txt points its sitemap at another origin → ${declared}`);
  } else if (!existsSync(path.join(dist, new URL(declared).pathname.slice(1)))) {
    errors.push(`robots.txt points at a sitemap that was not built → ${declared}`);
  }
  if (/^Disallow:\s*\/\s*$/m.test(robots)) {
    errors.push("robots.txt disallows the whole site");
  }
}

console.log(
  `checked ${checked} internal links and ${appLinks} links into the app ` +
    `across ${pages.length} pages, and ${ldNodes} structured-data nodes`,
);
if (errors.length) {
  console.error(`\n${errors.length} problem(s):`);
  for (const e of errors) console.error(`  ✗ ${e}`);
  process.exit(1);
}
console.log(
  "✓ all internal links resolve, every link into the app opens in a new tab, " +
    "and every page has its SEO tags",
);
