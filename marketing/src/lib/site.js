/**
 * Site-wide facts, in one place.
 *
 * Everything here is real and checked against the product. The pricing block
 * mirrors `backend/services/tokens.py` (PACKS and _DEFAULT_COSTS) and the
 * pipeline mirrors the stage table in README.md. When those change, this file
 * is the one to update — no page hardcodes a number.
 */

export const SITE_URL = import.meta.env?.SITE || "https://thryftshop.com";

/**
 * The live product. Every CTA on the site is this constant — including the one
 * place it is shown as visible text (mobile.astro), so there is no second copy
 * to forget.
 *
 * The app answers on BOTH this hostname and listing-lfwjrg.fly.dev; the Fly
 * certificate that makes the handshake succeed here is created by the app's own
 * deploy workflow (.github/workflows/deploy.yml), not by hand.
 */
export const APP_URL = "https://app.thryftshop.com";

/**
 * The two ways in. The app's auth dialog has Log in / Sign up tabs and opens on
 * Log in, so a visitor who came here to create an account would land on the
 * wrong one — hence a separate entry point rather than one "open the app".
 *
 * `?signup=1` is what the app reads to select the Sign up tab. Until the
 * app-side change ships it is simply an unknown query param: the link still
 * works and still opens the dialog, just on Log in — the same place "Open the
 * app" used to land, so nothing is worse in the meantime.
 */
export const LOGIN_URL = APP_URL;
export const SIGNUP_URL = `${APP_URL}/?signup=1`;

export const site = {
  name: "Thryft Shop",
  tagline: "Snap it · AI writes it · list it everywhere.",
  description:
    "Turn product photos into complete, ready-to-publish listings on eBay, " +
    "Etsy and Depop — individually or all at once.",
  // PLACEHOLDER — a company address on the real domain replaces this before
  // launch. The founder's personal inbox is deliberately not published here.
  supportEmail: "support@thryftshop.com",
  // PLACEHOLDER — the public TestFlight invite link.
  testflightUrl: "",
  bundleId: "com.thryftshop.app",
};

export const nav = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/pricing", label: "Pricing" },
  { href: "/mobile", label: "Apps" },
  { href: "/security", label: "Security" },
  { href: "/blog", label: "Blog" },
];

export const footerNav = [
  {
    title: "Product",
    links: [
      { href: "/how-it-works", label: "How it works" },
      { href: "/pricing", label: "Pricing" },
      { href: "/mobile", label: "iOS & Android" },
      { href: "/changelog", label: "Changelog" },
    ],
  },
  {
    title: "Company",
    links: [
      { href: "/about", label: "About" },
      { href: "/blog", label: "Blog" },
      { href: "/faq", label: "Support & FAQ" },
      { href: `mailto:${site.supportEmail}`, label: "Contact" },
    ],
  },
  {
    title: "Trust",
    links: [
      { href: "/security", label: "Security & privacy" },
      { href: "/privacy", label: "Privacy Policy" },
      { href: "/terms", label: "Terms of Service" },
    ],
  },
];

/**
 * The marketplaces, and how available each one actually is.
 *
 * This is not aspirational. eBay is open to anyone. Etsy is approved at Etsy's
 * PERSONAL tier (fly.toml: ETSY_ACCESS_TIER = 'personal'), which seats a
 * handful of shops rather than everyone, so it ships as a beta. Depop's
 * Selling API is partner-gated and those credentials have not been granted
 * yet, so it is not connectable at all.
 *
 * Promising a marketplace a visitor cannot actually connect is the one thing
 * a page like this must not do — they would sign up for it and hit a wall.
 * Update `status` here when a tier changes and every page follows.
 */
export const marketplaces = [
  {
    name: "eBay",
    status: "live",
    note: "Drafts or live listings, with business policies, category resolution and two-way sync.",
  },
  {
    name: "Etsy",
    status: "beta",
    note: "Draft, then activate — shop id and shipping defaults remembered. Open to a limited group of shops while we test it.",
  },
  {
    name: "Depop",
    status: "soon",
    note: "Built and waiting on Depop partner access. It publishes from the same draft the moment that lands.",
  },
];

/** Badge styling per availability. `live` gets no badge — it is the default. */
export const marketplaceStatus = {
  live: null,
  beta: { label: "Beta", class: "bg-yellow-soft text-yellow-ink" },
  soon: { label: "Coming soon", class: "bg-blue-soft text-blue-ink" },
};

/**
 * The pipeline, stage by stage. Mirrors the table in README.md — these are the
 * real implementation stages, not a marketing simplification.
 */
export const pipeline = [
  {
    key: "capture",
    step: "01",
    title: "Snap it",
    blurb:
      "Photograph the item, or upload shots you already have. One item or a whole pile — a bulk batch turns a photo dump into one draft per item, grouping the shots that belong together.",
    detail: "Up to 8 photos per listing · HEIC and JPEG",
  },
  {
    key: "optimize",
    step: "02",
    title: "Cleaned up for the marketplace",
    blurb:
      "Auto-orient, cut the background onto a white canvas with a soft contact shadow, square-frame the item at the photo's own scale — never a crop-in zoom — resize to 1600px and finish with a sharpen.",
    detail: "Pillow · eBay's own image recommendations",
  },
  {
    key: "identify",
    step: "03",
    title: "AI writes it",
    blurb:
      "Claude reads the photos and returns a structured draft: title, description, item specifics, condition and a suggested price — plus a confidence score and an explicit list of what it could not tell from the pictures.",
    detail: "Claude vision · reads size and care tags",
  },
  {
    key: "preview",
    step: "04",
    title: "You have the last word",
    blurb:
      "Every field is editable. Change them by hand, add or remove item specifics, or just say what you want — \"make it sound more vintage\", \"mention the small mark on the sleeve\" — and the draft rewrites itself.",
    detail: "Natural-language refine · nothing publishes unreviewed",
  },
  {
    key: "publish",
    step: "05",
    title: "List it everywhere",
    blurb:
      "Push to eBay, Etsy and Depop at once. Each marketplace succeeds or fails on its own, so one rejection never costs you the others — and you are told exactly which landed.",
    detail: "eBay Trading API · Etsy · Depop",
  },
];

/** Capabilities past the core flow. All shipped, all real. */
export const features = [
  {
    title: "Shop Mode",
    blurb:
      "Scan a thrift shelf with your camera and get resale estimates back before you buy. Sourcing decisions made standing in the aisle.",
    accent: "yellow",
  },
  {
    title: "Bulk drafts",
    blurb:
      "One pile of photos becomes many listings. The batch groups shots by item, so twenty photos of six things become six drafts, not twenty.",
    accent: "blue",
  },
  {
    title: "Two-way eBay sync",
    blurb:
      "Imports the listings you already have — including ones this app never created — so your whole store is in one place, not just what you listed here.",
    accent: "green",
  },
  {
    title: "Duplicate detection",
    blurb:
      "Finds live listings that look like the same item listed twice, and merges them back together in a click.",
    accent: "red",
  },
  {
    title: "What to do next",
    blurb:
      "Ranked, specific actions across your whole store — promote these, lower the price on those — each one applicable in bulk instead of listing by listing.",
    accent: "blue",
  },
  {
    title: "Photo tools",
    blurb:
      "Background removal, auto-clean and smart crop on any photo, with a safety pass that refuses a cutout which ate part of your product.",
    accent: "green",
  },
];

/* ---------------------------------------------------------------------------
   Pricing — mirrors backend/services/tokens.py
--------------------------------------------------------------------------- */

export const FREE_TOKENS_PER_MONTH = 50;

export const packs = [
  { id: "starter", label: "Starter", tokens: 50, usd: 5.99 },
  { id: "plus", label: "Plus", tokens: 120, usd: 11.99, popular: true },
  { id: "pro", label: "Pro", tokens: 300, usd: 24.99 },
  { id: "power", label: "Power seller", tokens: 1000, usd: 69.99 },
];

export const tokenCosts = [
  { feature: "A full AI listing draft", tokens: 5, note: "Identify, category, item specifics, tag read and maker check — the whole draft. Same price inside a bulk batch." },
  { feature: "Autofill item specifics", tokens: 2, note: "The standalone button. Bundled free inside a full draft." },
  { feature: "Shop Mode shelf scan", tokens: 2, note: "One video's frames, scanned for resale estimates." },
  { feature: "AI refine instruction", tokens: 1, note: "A free-form \"make it…\" edit on a draft you already have." },
  { feature: "AI photo tool", tokens: 1, note: "Per photo: background removal, auto-clean or smart crop." },
];

/** Rounded per-token price, for the pack cards. */
export function perToken(pack) {
  return (pack.usd / pack.tokens).toFixed(2);
}

/** Roughly how many full listing drafts a token count buys. */
export function draftsFor(tokens) {
  return Math.floor(tokens / 5);
}
