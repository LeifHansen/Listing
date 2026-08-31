/**
 * Site-wide facts, in one place.
 *
 * Everything here is real and checked against the product. The pricing block
 * mirrors `backend/services/tokens.py` (PACKS and _DEFAULT_COSTS) and the
 * pipeline mirrors the stage table in README.md. When those change, this file
 * is the one to update — no page hardcodes a number.
 */

export const SITE_URL = import.meta.env?.SITE || "https://thryft.shop";

/** The live product. Real today; becomes app.<domain> when the domain lands. */
export const APP_URL = "https://listing-lfwjrg.fly.dev";

export const site = {
  name: "Thryft Shop",
  tagline: "Snap it · AI writes it · list it everywhere.",
  description:
    "Turn product photos into complete, ready-to-publish listings on eBay, " +
    "Etsy and Depop — individually or all at once.",
  // PLACEHOLDER — a company address on the real domain replaces this before
  // launch. The founder's personal inbox is deliberately not published here.
  supportEmail: "support@thryft.shop",
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
      { href: `mailto:${"support@thryft.shop"}`, label: "Contact" },
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

/** The marketplaces a listing can be published to today. */
export const marketplaces = [
  { name: "eBay", note: "Drafts or live listings, with business policies and category resolution" },
  { name: "Etsy", note: "Draft, then activate — shop id and shipping defaults remembered" },
  { name: "Depop", note: "Published straight from the same draft" },
];

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
