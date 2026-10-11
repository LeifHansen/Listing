// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import tailwindcss from "@tailwindcss/vite";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// The public origin the site is served from. Every canonical URL, OG image
// URL, sitemap entry and RSS link is derived from this one value, so pointing
// the site at a real domain (or at a *.pages.dev preview) is a single change.
const SITE = process.env.SITE_URL || "https://thryftshop.com";

/* ---------------------------------------------------------------------------
   Sitemap metadata

   `lastmod` is the one sitemap field crawlers still act on — it is how Google
   decides a page is worth re-fetching. It is also the one that is easiest to
   discredit: a sitemap that stamps every URL with the build time claims the
   whole site changed on every deploy, and a crawler that checks twice and
   finds nothing different stops believing the field at all.

   So it is only emitted where a real date exists: content with a `date` or
   `updated` in its frontmatter, and the index pages that list that content
   (whose newest entry IS their last modification). Hand-written marketing
   pages get no `lastmod` rather than a fictional one.

   Read straight off disk because this file runs before `astro:content` exists.
--------------------------------------------------------------------------- */

const contentDir = path.resolve(import.meta.dirname, "src/content");

/** Frontmatter dates out of every markdown file in a collection directory. */
function collection(name) {
  let files;
  try {
    files = readdirSync(path.join(contentDir, name)).filter((f) => f.endsWith(".md"));
  } catch {
    return [];
  }
  return files.map((file) => {
    const raw = readFileSync(path.join(contentDir, name, file), "utf8");
    const frontmatter = raw.split(/^---\s*$/m)[1] ?? "";
    const field = (key) => frontmatter.match(new RegExp(`^${key}:\\s*(\\S+)`, "m"))?.[1];
    return {
      slug: file.replace(/\.md$/, ""),
      draft: /^draft:\s*true\s*$/m.test(frontmatter),
      // `updated` wins where a post carries one: a revised post's last
      // modification is the revision, not the original publication.
      date: field("updated") || field("date"),
    };
  });
}

/** The newest date in a list, for the index page that lists them. */
function newest(entries) {
  return entries.map((e) => e.date).filter(Boolean).sort().at(-1);
}

const blog = collection("blog").filter((e) => !e.draft);
const changelog = collection("changelog");
const legal = collection("legal");

/** pathname → ISO date. Only paths whose date is a real fact appear here. */
const LASTMOD = Object.fromEntries(
  [
    ...blog.map((e) => [`/blog/${e.slug}`, e.date]),
    ["/blog", newest(blog)],
    ["/changelog", newest(changelog)],
    ...legal.map((e) => [e.slug === "privacy" ? "/privacy" : `/${e.slug}`, e.date]),
  ].filter(([, date]) => Boolean(date)),
);

/**
 * Relative importance within the site. Google has said for years that it
 * ignores this, and it is kept because other crawlers do not and it costs
 * nothing — not because it is expected to move a ranking.
 */
const PRIORITY = {
  "/": 1.0,
  "/how-it-works": 0.9,
  "/pricing": 0.9,
  "/mobile": 0.8,
  "/security": 0.8,
  "/faq": 0.8,
  "/blog": 0.8,
  "/about": 0.6,
  "/changelog": 0.6,
  "/privacy": 0.3,
  "/terms": 0.3,
};

export default defineConfig({
  site: SITE,
  trailingSlash: "never",
  integrations: [
    sitemap({
      serialize(item) {
        const pathname = new URL(item.url).pathname.replace(/(.+)\/$/, "$1");
        const lastmod = LASTMOD[pathname];
        return {
          ...item,
          ...(lastmod ? { lastmod: new Date(lastmod).toISOString() } : {}),
          priority: PRIORITY[pathname] ?? (pathname.startsWith("/blog/") ? 0.7 : 0.5),
        };
      },
    }),
  ],
  build: {
    // Emit `/pricing.html` rather than `/pricing/index.html` so the static
    // host serves clean URLs without a redirect hop.
    format: "file",
    inlineStylesheets: "auto",
  },
  vite: {
    plugins: [tailwindcss()],
    // The brand tokens are imported from ../frontend, which is outside this
    // project root. Vite refuses to serve files outside the root in dev
    // unless the parent is explicitly allowed.
    server: { fs: { allow: [".."] } },
  },
});
