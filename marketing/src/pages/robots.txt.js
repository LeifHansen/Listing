/**
 * robots.txt as a route rather than a static file, so the sitemap URL is
 * derived from the configured origin instead of being a second copy of it
 * that goes stale the moment SITE_URL changes.
 *
 * This is a public marketing site: there is nothing here that should not be
 * crawled, and the whole point of it is to be found. So the policy is "yes",
 * said explicitly rather than by omission.
 *
 * The named answer-engine crawlers are listed deliberately. They all read this
 * file, they are an increasing share of how anyone finds a tool like this, and
 * a blanket `User-agent: *` only covers them until someone adds a disallow for
 * a directory and accidentally shuts them out of it. Naming them means the
 * decision to let them in is written down rather than inherited.
 *
 * Note what this file cannot do: robots.txt controls CRAWLING, not indexing. A
 * disallowed URL can still appear in results if something links to it. The
 * page that must stay out of an index (the 404) carries `noindex` in its head,
 * which is the directive that actually works — see src/layouts/Base.astro.
 */

/** Crawlers that read and answer questions, rather than build a search index. */
const ANSWER_ENGINES = [
  "ChatGPT-User",
  "OAI-SearchBot",
  "GPTBot",
  "ClaudeBot",
  "Claude-User",
  "Claude-SearchBot",
  "PerplexityBot",
  "Perplexity-User",
  "Google-Extended",
  "Applebot-Extended",
];

export function GET(context) {
  const url = (p) => new URL(p, context.site).href;

  const body = [
    "# Everything here is public. Crawl all of it.",
    "User-agent: *",
    "Allow: /",
    "",
    "# Answer engines are welcome too — see /llms.txt for the short version",
    "# of what this product is, written for them.",
    ...ANSWER_ENGINES.flatMap((agent) => [`User-agent: ${agent}`, "Allow: /", ""]),
    `Sitemap: ${url("sitemap-index.xml")}`,
    "",
  ].join("\n");

  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}
