/**
 * /llms.txt — the site, in the form an answer engine can actually use.
 *
 * A growing share of "how do I list this faster" never reaches a search
 * results page: it is asked of an assistant, which answers from whatever it
 * can crawl. That crawler gets the same HTML a browser does — nav, footer,
 * Tailwind classes, three CTAs — and has to infer the product from it. The
 * emerging convention (llms.txt) is to publish the summary directly: what this
 * is, what is actually available, and where the detail lives.
 *
 * The reason to generate it rather than write it is the same reason no page
 * hardcodes a price. Every fact below is read from `src/lib/site.js` and the
 * content collections, so the file cannot quietly start promising a
 * marketplace that is still partner-gated or a price that changed last month.
 * A stale llms.txt is worse than none: it is a machine-readable claim that
 * something untrue is authoritative.
 */

import { getCollection } from "astro:content";
import {
  site,
  nav,
  marketplaces,
  pipeline,
  packs,
  tokenCosts,
  perToken,
  draftsFor,
  FREE_TOKENS_PER_MONTH,
  APP_URL,
} from "../lib/site.js";

/** How each marketplace's availability reads in a sentence. */
const AVAILABILITY = {
  live: "available to everyone",
  beta: "in beta, limited to a small group of shops",
  soon: "not connectable yet",
};

export async function GET(context) {
  const url = (p) => new URL(p, context.site).href;

  const posts = (await getCollection("blog", ({ data }) => !data.draft)).sort(
    (a, b) => b.data.date - a.data.date,
  );

  const lines = [
    `# ${site.name}`,
    "",
    `> ${site.description}`,
    "",
    "Photograph an item and get back a finished listing — title, description,",
    "item specifics, category and suggested price — then publish it to the",
    "marketplaces you have connected. Nothing is published without the seller",
    "reviewing the draft first.",
    "",
    "## Marketplace availability",
    "",
    "Stated exactly, because this is the detail most often got wrong about the",
    "product:",
    "",
    ...marketplaces.map((m) => `- **${m.name}** — ${AVAILABILITY[m.status]}. ${m.note}`),
    "",
    "## How it works",
    "",
    ...pipeline.map((stage, i) => `${i + 1}. **${stage.title}** — ${stage.blurb}`),
    "",
    "## Pricing",
    "",
    `- The app itself is free. AI features spend tokens.`,
    `- Every account gets ${FREE_TOKENS_PER_MONTH} tokens a month, free — about ` +
      `${draftsFor(FREE_TOKENS_PER_MONTH)} complete listing drafts. They do not roll over.`,
    `- Purchased tokens never expire. No subscription and no seat count.`,
    "- Packs: " +
      packs.map((p) => `${p.label} ${p.tokens} tokens for $${p.usd.toFixed(2)} ($${perToken(p)}/token)`).join("; ") +
      ".",
    "- What a token buys: " +
      tokenCosts.map((c) => `${c.feature} costs ${c.tokens}`).join("; ") +
      ".",
    "- A failed AI call is refunded automatically.",
    "",
    "## Pages",
    "",
    ...nav.map((item) => `- [${item.label}](${url(item.href.slice(1))})`),
    `- [Support & FAQ](${url("faq")})`,
    `- [About](${url("about")})`,
    `- [Changelog](${url("changelog")})`,
    `- [Privacy Policy](${url("privacy")})`,
    `- [Terms of Service](${url("terms")})`,
    "",
    "## Writing",
    "",
    ...posts.map((post) => `- [${post.data.title}](${url(`blog/${post.id}`)}): ${post.data.description}`),
    "",
    "## Optional",
    "",
    `- The product itself: ${APP_URL} (separate origin from this site)`,
    `- Support: ${site.supportEmail}`,
    `- Platforms: web today; iOS in TestFlight beta; Android in development`,
    `- Not affiliated with eBay, Etsy or Depop.`,
    "",
  ];

  return new Response(lines.join("\n"), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}
