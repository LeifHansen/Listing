/**
 * robots.txt as a route rather than a static file, so the sitemap URL is
 * derived from the configured origin instead of being a second copy of it
 * that goes stale the moment SITE_URL changes.
 */
export function GET(context) {
  const sitemap = new URL("sitemap-index.xml", context.site).href;
  return new Response(
    `User-agent: *\nAllow: /\n\nSitemap: ${sitemap}\n`,
    { headers: { "Content-Type": "text/plain; charset=utf-8" } },
  );
}
