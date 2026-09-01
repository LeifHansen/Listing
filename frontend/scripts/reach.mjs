/* Can a seller actually TAP the publish bar? — a phone-sized reachability check.
 *
 * The editor's publish controls live in a bar pinned to the bottom of the
 * screen, which on a phone is the busiest strip of the whole app: the fixed
 * bottom nav sits there, and so does the toast stack. Both are drawn ABOVE the
 * bar, and both grow with the iPhone's home-indicator inset. When one of them
 * lands on a button, the button is still there, still enabled, still passing
 * every unit test — and a tap on it does nothing at all.
 *
 * That is not a hypothetical. An eBay rejection raises an error toast, error
 * toasts live 8 seconds, the toast is as wide as the screen, and eBay's own
 * rejection sentences run to three lines — 114px of pointer-catching panel
 * directly over "Publish Live". The seller taps publish, the tap dismisses the
 * toast, nothing else happens, and the message they were meant to read is gone
 * too. Publishing the same draft from its card in the drafts grid worked,
 * because that button sits in the page flow rather than under the bottom edge.
 *
 * So this asserts the one thing no unit test can see: for every control in the
 * publish bar, the topmost element at that control's own coordinates IS that
 * control — before a publish, and while the toast from a failed one is up.
 *
 *     npm run build && npm run reach
 *
 * It serves `dist` and answers the API itself, so it needs no backend. Headless
 * Chromium reports env(safe-area-inset-bottom) as 0, so the iPhone's inset is
 * applied here to every bottom-anchored fixed layer — that inset is what makes
 * the collision, and a check run without it passes on a phone that fails.
 */
import { chromium } from "playwright";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DIST = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "dist");
const PORT = Number(process.env.REACH_PORT || 4178);
// iPhone 14/15/16's home-indicator inset. Every current iPhone reports 34px
// here; the app's own bottom nav and toast stack both pad themselves by it.
const INSET = Number(process.env.REACH_INSET ?? 34);
const TYPES = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp",
  ".woff2": "font/woff2", ".json": "application/json",
};

if (!fs.existsSync(path.join(DIST, "index.html"))) {
  console.error("No dist/ — run `npm run build` first.");
  process.exit(1);
}

// One seller, one draft, eBay connected: the state the editor opens in.
const LISTING = {
  title: "Vintage Levi's 501 jeans", brand: "Levi's", condition: "USED_GOOD",
  category_suggestion: "Men's Jeans", category_id: "11483",
  description: "Straight leg, mid wash.", price: 45, currency: "USD", quantity: 1,
  listing_format: "FIXED_PRICE", package_weight_lb: 1, package_weight_oz: 4,
  fulfillment_policy_id: "fp-1",
  item_specifics: [{ name: "Brand", value: "Levi's", confidence: "high" }],
  images: [], image_urls: [], marketplaces: {},
};
const RECORD = { id: "l1", status: "draft", updated_at: "2026-08-30T10:00:00Z",
                 listing: LISTING, conflicts: [] };
// eBay's own words for a rejection it will not explain — the sentence the
// editor puts in the toast, and the reason that toast is three lines tall.
const REJECTION = {
  error: true, published: false, dry_run: false, mode: "live", issues: [],
  message: "The item cannot be listed or modified. The title and/or "
    + "description may contain improper words or the listing or seller may be "
    + "in violation of eBay policy.",
};
const API = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true, taxonomy_configured: true },
  "/api/ebay/status": { connected: true, env: "production", username: "seller", oauth_ready: true },
  "/api/listings/l1": RECORD,
  "/api/listings": { authed: true, db: { configured: true, connected: true }, listings: [RECORD] },
  "/api/notifications": { notifications: [], unread: 0 },
  "/api/marketplaces": { marketplaces: [{ key: "ebay", connected: true, label: "eBay" }] },
  "/api/tokens": { enabled: true, total: 250, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
  "/api/ebay/policies": { policies: {}, selected: {} },
};

const server = http.createServer((req, res) => {
  const asked = req.url.split("?")[0];
  let file = path.join(DIST, asked);
  if (!file.startsWith(DIST) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    file = path.join(DIST, "index.html");
  }
  res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "text/plain" });
  res.end(fs.readFileSync(file));
});
await new Promise((r) => server.listen(PORT, r));

const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM } : {});

// Both shapes the app is used in. The phone is where the collision was found,
// and the desktop leg is not a formality: the toast stack takes the
// bottom-right corner from `sm` up, which is the exact corner the publish bar
// puts its primary button in.
const SCREENS = [
  { name: "phone", viewport: { width: 390, height: 844 }, hasTouch: true, inset: INSET },
  { name: "desktop", viewport: { width: 1280, height: 900 }, hasTouch: false, inset: 0 },
];

const problems = [];
for (const screen of SCREENS) {
await runScreen(screen);
}

async function runScreen({ name, viewport, hasTouch, inset }) {
const ctx = await browser.newContext({ viewport, hasTouch });
const page = await ctx.newPage();
let publishes = 0;
await page.route("**/api/**", async (route) => {
  const p = new URL(route.request().url()).pathname;
  if (p === "/api/publish") publishes += 1;
  const body = p.startsWith("/api/publish") ? REJECTION
    : (API[Object.keys(API).sort((a, b) => b.length - a.length)
        .find((k) => p.startsWith(k))] ?? { ok: true });
  await route.fulfill({ status: 200, contentType: "application/json",
                        body: JSON.stringify(body) });
});

/* Give every bottom-anchored fixed layer the inset iOS gives it.
 *
 * Re-applied before each measurement rather than once: the toast stack is
 * portalled in when the first toast appears, and it is exactly the layer whose
 * inset decides whether the toast lands on the publish bar. */
const applyHomeIndicatorInset = (px) => page.evaluate((px_) => {
  for (const el of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(el);
    if (cs.position !== "fixed" || cs.bottom === "auto") continue;
    el.style.paddingBottom =
      `${Math.max(parseFloat(cs.paddingBottom) || 0, px_)}px`;
  }
}, px);

/* Every control in the publish bar that a tap cannot reach.
 *
 * Two points per control: its centre, and 6px above its bottom edge — the
 * bottom edge is where the nav, its raised "Sell" button and the toast stack
 * all arrive from, so a check that only probes centres passes on a button
 * whose bottom third is under something else. */
const unreachable = () => page.evaluate(() => {
  const bar = document.querySelector("[data-publish-bar]")
    || [...document.querySelectorAll("button")]
      .find((b) => /Publish Live|Update Live Listing/i.test(b.textContent))
      ?.closest("div.rounded-card");
  if (!bar) return [{ control: "the publish bar", why: "not on screen at all" }];
  const name = (el) =>
    (el.textContent || "").trim() || el.getAttribute("aria-label") || "?";
  const out = [];
  for (const control of bar.querySelectorAll("button")) {
    const r = control.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    for (const [where, x, y] of [
      ["centre", r.x + r.width / 2, r.y + r.height / 2],
      ["bottom edge", r.x + r.width / 2, r.bottom - 3],
    ]) {
      const on = document.elementFromPoint(x, y);
      if (on && (on === control || control.contains(on))) continue;
      const blocker = on?.closest("button");
      out.push({
        control: name(control), where,
        why: `covered by ${blocker ? `"${name(blocker)}"` : (on?.tagName || "nothing")}`
          + `${on?.closest('[role="alert"], [role="status"]') ? " (a toast)" : ""}`,
      });
    }
  }
  return out;
});

try {
  await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "networkidle", timeout: 30000 });
  // Visible-only: the phone nav and the sidebar both carry a Sell button, and
  // whichever one this screen hides is still in the DOM. The sidebar's also
  // wears a draft count ("Sell1"), so the name is matched by prefix.
  await page.getByRole("button", { name: /^Sell/ })
    .filter({ visible: true }).first().click();
  await page.waitForTimeout(600);
  await page.getByRole("button", { name: /Review & List/i }).first().click();
  await page.waitForTimeout(1200);

  await applyHomeIndicatorInset(inset);
  problems.push([`${name}: publish bar is tappable`, await unreachable()]);

  // Now the case a seller actually hits: publish, eBay refuses, the editor
  // raises the error toast — and the seller reaches straight back for the
  // same button to try again.
  await page.getByRole("button", { name: "Publish Live", exact: true }).click();
  await page.waitForTimeout(1200);
  await applyHomeIndicatorInset(inset);
  const toast = await page.locator('[role="alert"]').count();
  problems.push([`${name}: a rejection raises the error toast`,
    toast ? [] : ["no toast after a refused publish — the seller was told nothing"]]);
  problems.push([`${name}: publish bar is still tappable under the toast`,
    await unreachable()]);

  // The bug as the seller met it: press publish again while the rejection is
  // still on screen. Hit-testing says the button is on top; this says the tap
  // actually reaches it, which is the only part the seller cares about.
  const before = publishes;
  await page.getByRole("button", { name: "Publish Live", exact: true })
    .click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1500);
  problems.push([`${name}: pressing publish again while the toast is up publishes again`,
    publishes > before ? [] : ["the tap did nothing — no publish was sent"]]);

  // And the toast can still be got rid of deliberately — one fewer on screen,
  // not none: the republish above raised its own rejection toast behind this
  // one, which is exactly what a seller retrying would be looking at.
  const standing = await page.locator('[role="alert"]').count();
  await page.locator('[role="alert"] button[aria-label="Dismiss"]').first()
    .click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(800);
  const left = await page.locator('[role="alert"]').count();
  problems.push([`${name}: the toast can be dismissed`,
    left === standing - 1 ? []
      : [`dismiss left ${left} toast(s) on screen, expected ${standing - 1}`]]);
} catch (e) {
  problems.push([`${name}: reachability run`, [e.message.slice(0, 300)]]);
}
await ctx.close();
}

await browser.close();
server.close();

let bad = 0;
for (const [name, errs] of problems) {
  if (errs.length) {
    bad++;
    console.log(`FAIL ${name}`);
    for (const e of errs) {
      console.log(`     ${typeof e === "string" ? e : `${e.control} (${e.where}): ${e.why}`}`);
    }
  } else console.log(`ok   ${name}`);
}
console.log(bad ? `\n${bad} check(s) with problems` : "\nall checks clean");
process.exit(bad ? 1 : 0);
