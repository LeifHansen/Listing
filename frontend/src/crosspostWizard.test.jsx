/* Crossposting an eBay store to Etsy, from the seller's side.
 *
 * The wizard's job is to make sure nothing is sent that Etsy would refuse
 * and nothing is sent that the seller has not seen. Four things are pinned
 * here: the batch answers reach every row, a listing Etsy still needs
 * something from cannot be sent, drafts are the default and going live says
 * what it costs, and the run reports each listing's own outcome.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { CrosspostWizard } from "@/views/crosspost/CrosspostWizard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const SETTINGS = {
  shipping_profiles: [{ id: "7", name: "Standard" }],
  return_policies: [{ id: "8", name: "Returns" }],
  readiness_states: [{ id: "9", name: "1–3 days" }],
  selected: { shipping_profile_id: "7", return_policy_id: "8", readiness_state_id: "9" },
};

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true },
  "/api/ebay/policies": { policies: [] },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [
    { key: "ebay", label: "eBay", connected: true, oauth_ready: true, supports: {} },
    { key: "etsy", label: "Etsy", connected: true, oauth_ready: true, supports: {} }] },
  "/api/etsy/settings-options": SETTINGS,
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
  "/api/listings": { authed: true, db: { configured: true, connected: true }, listings: [] },
};

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

const live = (id, over = {}) => ({
  id, status: "published", updated_at: "2026-09-01T00:00:00Z",
  listing: {
    title: `Item ${id}`, description: "Nice.", price: 20, quantity: 1,
    images: ["a.jpg"], marketplaces: { ebay: { status: "published" } },
    etsy: { taxonomy_id: 1, who_made: "someone_else", when_made: "1990s" },
    ...over,
  },
});

let host;
let root;
let sent;

function server(over = {}) {
  return (url, opts = {}) => {
    const path = String(url);
    sent.push([path, opts.body ? JSON.parse(opts.body) : null]);
    if (over[path]) return json(over[path](sent.length));
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

async function mount(items, over) {
  sent = [];
  vi.stubGlobal("fetch", vi.fn(server(over)));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <CrosspostWizard items={items} onClose={() => {}} />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const dialog = () => document.querySelector("[role=dialog]") || document.body;
const button = (text) => [...dialog().querySelectorAll("button")]
  .find((b) => b.textContent.includes(text));
const click = async (el) => { await act(async () => { el.click(); }); };
const tick = async () => { await act(async () => { await new Promise((r) => setTimeout(r, 20)); }); };

describe("the crosspost wizard", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => {
    await act(async () => { root.unmount(); });
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("says which listings can go, and why the rest can't", async () => {
    await mount([
      live("a"),
      live("b", { listing_format: "AUCTION" }),
      live("c", { marketplaces: { etsy: { status: "published", listing_id: "9" } } }),
    ]);
    expect(dialog().querySelector("[data-tally]").textContent)
      .toContain("1 ready · 0 need something · 2 skipped");
    const skips = [...dialog().querySelectorAll("[data-skip]")].map((n) => n.textContent);
    expect(skips.some((s) => /auction/i.test(s))).toBe(true);
    expect(skips.some((s) => /already on etsy/i.test(s))).toBe(true);
    expect(button("Continue (1)")).toBeTruthy();
  });

  it("will not review until the seller attests to Etsy's rule", async () => {
    await mount([live("a")]);
    await click(button("Continue"));
    const review = button("Review 1");
    expect(review.disabled).toBe(true);
    const attest = [...dialog().querySelectorAll("input[type=checkbox]")].at(-1);
    await click(attest);
    expect(button("Review 1").disabled).toBe(false);
  });

  it("sends the batch answers to the review and defaults to drafts", async () => {
    await mount([live("a")], {
      "/api/crosspost/etsy/review": () => ({
        rows: [{ id: "a", title: "Item a", etsy_title: "Item a", ready: true,
                 taxonomy: { id: 5, path: "Home > Mugs", source: "ebay_path" },
                 who_made: "someone_else", when_made: "1990s",
                 when_made_source: "details", tags: ["mug"], materials: [],
                 etsy: {}, blockers: [], warnings: [], policy_flag: false }],
        skipped: [], ai_calls: 0, mode: "draft" }),
    });
    await click(button("Continue"));
    await click([...dialog().querySelectorAll("input[type=checkbox]")].at(-1));
    await click(button("Review 1"));
    await tick();
    const [path, body] = sent.find(([p]) => p === "/api/crosspost/etsy/review");
    expect(path).toBe("/api/crosspost/etsy/review");
    expect(body.mode).toBe("draft");
    expect(body.defaults.who_made).toBe("someone_else");
    expect(body.listing_ids).toEqual(["a"]);
    expect(dialog().textContent).toContain("Sending as Etsy drafts");
    expect(dialog().querySelector("[data-review-tally]").textContent).toContain("1 ready to send");
  });

  it("names the fee before anything goes live", async () => {
    await mount([live("a"), live("b")]);
    await click(button("Continue"));
    await click(button("Live on Etsy"));
    expect(dialog().textContent).toMatch(/listing fee for each one that goes live — that's 2 listings/);
  });

  it("refuses to send a row Etsy still needs something from", async () => {
    await mount([live("a")], {
      "/api/crosspost/etsy/review": () => ({
        rows: [{ id: "a", title: "Item a", etsy_title: "Item a", ready: false,
                 taxonomy: { id: 0, path: "", source: "" },
                 who_made: "someone_else", when_made: "", when_made_source: "",
                 tags: [], materials: [], etsy: {}, warnings: [], policy_flag: false,
                 blockers: [{ target: "etsy_attribution", title: "When was it made?",
                              fix: "Set when the item was made." }] }],
        skipped: [], ai_calls: 0, mode: "draft" }),
    });
    await click(button("Continue"));
    await click([...dialog().querySelectorAll("input[type=checkbox]")].at(-1));
    await click(button("Review 1"));
    await tick();
    expect(dialog().textContent).toContain("When was it made?");
    expect(button("Crosspost 0").disabled).toBe(true);
    expect(sent.some(([p]) => p.startsWith("/api/crosspost/etsy/start"))).toBe(false);
  });

  it("reports each listing's own outcome from the run", async () => {
    let polls = 0;
    await mount([live("a"), live("b")], {
      "/api/crosspost/etsy/review": () => ({
        rows: ["a", "b"].map((id) => ({
          id, title: `Item ${id}`, etsy_title: `Item ${id}`, ready: true,
          taxonomy: { id: 5, path: "Home > Mugs", source: "ebay_path" },
          who_made: "someone_else", when_made: "1990s", when_made_source: "details",
          tags: [], materials: [], etsy: {}, blockers: [], warnings: [],
          policy_flag: false })),
        skipped: [], ai_calls: 0, mode: "draft" }),
      "/api/crosspost/etsy/start": () => ({ job_id: "job-1", running: true }),
      "/api/bulk/status/job-1": () => {
        polls += 1;
        return {
          done: polls > 1, current: polls > 1 ? 2 : 1, total_items: 2,
          items: [
            { id: "a", title: "Item a", status: "draft", url: "https://etsy/a" },
            { id: "b", title: "Item b",
              status: polls > 1 ? "refused" : "queued", message: polls > 1 ? "Etsy said no." : "" },
          ],
        };
      },
    });
    await click(button("Continue"));
    await click([...dialog().querySelectorAll("input[type=checkbox]")].at(-1));
    await click(button("Review 2"));
    await tick();
    await click(button("Crosspost 2"));
    for (let i = 0; i < 40 && !/2 of 2 sent/.test(dialog().textContent); i += 1) {
      await act(async () => { await new Promise((r) => setTimeout(r, 60)); });
    }
    const [, startBody] = sent.find(([p]) => p === "/api/crosspost/etsy/start");
    expect(startBody.items.map((i) => i.id)).toEqual(["a", "b"]);
    expect(startBody.items[0].etsy.taxonomy_id).toBe(5);
    expect(dialog().textContent).toContain("Draft created on Etsy");
    expect(dialog().textContent).toContain("Etsy refused it");
    expect(dialog().textContent).toContain("Etsy said no.");
    expect(dialog().querySelector("a[href='https://etsy/a']")).toBeTruthy();
  });
});
