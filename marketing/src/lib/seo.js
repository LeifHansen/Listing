/**
 * Everything a page needs to be understood by a crawler, in one place.
 *
 * Two jobs:
 *
 *  1. **Breadcrumbs.** Derived from the URL path rather than declared per page,
 *     so a new route gets a correct trail without anyone remembering to add
 *     one. The labels come from `nav`/`footerNav` in site.js — the same strings
 *     a human sees in the header and footer — so the trail can never disagree
 *     with the navigation.
 *
 *  2. **The JSON-LD graph.** One `@graph` per page instead of a scatter of
 *     unrelated `<script type="application/ld+json">` blocks. The difference
 *     matters: separate blocks describe an organization, a page and an article
 *     that a parser has no reason to believe are related, while a graph with
 *     `@id` references says *this* article is published by *this* organization
 *     and appears on *this* page. That is what lets a rich result, or an answer
 *     engine citing the site, attribute anything correctly.
 *
 * Node ids are stable URLs with a fragment (`…/#organization`), which is the
 * convention every consumer expects and the reason a page can reference the
 * organization without restating it.
 */

import { site, nav, footerNav } from "./site.js";

/** The one language this site is written in. */
export const LOCALE = "en-US";
/** Open Graph spells it with an underscore; schema.org and HTML use the dash. */
export const OG_LOCALE = "en_US";

/**
 * Path → label, built from the navigation rather than hand-maintained.
 *
 * Every route that appears in the header or footer is covered automatically,
 * which today is every route except the 404. Anything missing falls back to a
 * de-slugified segment, so an unlisted page still gets a sensible crumb.
 */
const NAV_LABELS = Object.fromEntries(
  [...nav, ...footerNav.flatMap((group) => group.links)]
    .filter((link) => link.href.startsWith("/"))
    .map((link) => [link.href.replace(/\/$/, ""), link.label]),
);

/** "how-it-works" → "How it works". Last resort when nav has no label. */
function deslugify(segment) {
  const words = segment.replace(/[-_]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * The trail for a page, as `[{ name, path }]`, starting at the home page.
 *
 * The final crumb is the page itself and uses its own `title` — a blog post's
 * slug is not its name. Passing a title for a route that IS in the nav does not
 * override the nav label for the intermediate crumbs, only the last one.
 *
 * The home page gets an empty trail: a one-item breadcrumb is noise, and
 * Google ignores it.
 */
export function breadcrumbsFor(pathname, title) {
  const clean = pathname.replace(/\.html$/, "").replace(/\/index$/, "/");
  const segments = clean.split("/").filter(Boolean);
  if (segments.length === 0) return [];

  const trail = [{ name: "Home", path: "/" }];
  let prefix = "";
  segments.forEach((segment, i) => {
    prefix += `/${segment}`;
    const last = i === segments.length - 1;
    const name = last
      ? title || NAV_LABELS[prefix] || deslugify(segment)
      : NAV_LABELS[prefix] || deslugify(segment);
    trail.push({ name, path: prefix });
  });
  return trail;
}

/**
 * The `@id`s of the nodes Base.astro always emits, so a page-level node can
 * point at them (`publisher: { "@id": ids.organization }`) instead of
 * describing the same organization a second time.
 */
export function nodeIds(origin, canonical) {
  return {
    organization: `${origin}#organization`,
    website: `${origin}#website`,
    logo: `${origin}#logo`,
    webpage: `${canonical}#webpage`,
    breadcrumb: `${canonical}#breadcrumb`,
    primaryImage: `${canonical}#primaryimage`,
  };
}

/**
 * The nodes every page carries: who publishes the site, what the site is, what
 * this page is, and where it sits.
 *
 * `article` (a `{ published, modified }` pair) upgrades the WebPage's dates —
 * a page whose content has a real publication date should say so even when the
 * article node itself is supplied separately by the page.
 */
export function baseGraph({ origin, canonical, name, description, image, imageAlt, breadcrumbs, article }) {
  const ids = nodeIds(origin, canonical);
  const logoUrl = new URL("/logo-512.webp", origin).href;

  const organization = {
    "@type": "Organization",
    "@id": ids.organization,
    name: site.name,
    url: origin,
    description: site.description,
    slogan: site.tagline,
    email: site.supportEmail,
    logo: {
      "@type": "ImageObject",
      "@id": ids.logo,
      url: logoUrl,
      contentUrl: logoUrl,
      width: 512,
      height: 512,
      caption: site.name,
    },
    image: { "@id": ids.logo },
    // Only real profiles, never a placeholder: a `sameAs` pointing at a handle
    // nobody owns is worse than no `sameAs` at all.
    ...(site.sameAs.length ? { sameAs: site.sameAs } : {}),
  };

  const website = {
    "@type": "WebSite",
    "@id": ids.website,
    url: origin,
    name: site.name,
    description: site.description,
    publisher: { "@id": ids.organization },
    inLanguage: LOCALE,
  };

  const primaryImage = {
    "@type": "ImageObject",
    "@id": ids.primaryImage,
    url: image,
    contentUrl: image,
    width: 1200,
    height: 630,
    ...(imageAlt ? { caption: imageAlt } : {}),
  };

  const webpage = {
    "@type": "WebPage",
    "@id": ids.webpage,
    url: canonical,
    name,
    description,
    isPartOf: { "@id": ids.website },
    about: { "@id": ids.organization },
    primaryImageOfPage: { "@id": ids.primaryImage },
    inLanguage: LOCALE,
    ...(article?.published ? { datePublished: article.published } : {}),
    ...(article?.modified ? { dateModified: article.modified } : {}),
    ...(breadcrumbs.length ? { breadcrumb: { "@id": ids.breadcrumb } } : {}),
  };

  const nodes = [organization, website, primaryImage, webpage];

  if (breadcrumbs.length) {
    nodes.push({
      "@type": "BreadcrumbList",
      "@id": ids.breadcrumb,
      itemListElement: breadcrumbs.map((crumb, i) => ({
        "@type": "ListItem",
        position: i + 1,
        name: crumb.name,
        item: new URL(crumb.path, origin).href,
      })),
    });
  }

  return nodes;
}
