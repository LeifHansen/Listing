// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import tailwindcss from "@tailwindcss/vite";

// The public origin the site is served from. Every canonical URL, OG image
// URL, sitemap entry and RSS link is derived from this one value, so pointing
// the site at a real domain (or at a *.pages.dev preview) is a single change.
const SITE = process.env.SITE_URL || "https://thryft.shop";

export default defineConfig({
  site: SITE,
  trailingSlash: "never",
  integrations: [sitemap()],
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
