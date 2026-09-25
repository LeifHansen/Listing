/* A listing can be changed from its card on Manage, without opening it.
 *
 * The grid could only open a live listing: the draft controls were kept off
 * live cards because a number changed there was saved here and nowhere else.
 * Each card now carries a collapsed "Quick edit" toggle — a strip along a
 * grid tile's foot, a button among a list row's controls — that opens the
 * fields a seller changes most, right under the card. A save sends only what
 * was touched, and a live listing's change goes to eBay in the same request
 * (POST /api/listings/{id}/quick-edit), so the card and the listing agree.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { ListingsView } from "@/views/ListingsView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true, username: "leif" },
  "/api/ebay/policies": { policies: { fulfillment: [] } },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
};

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

const live = (id, title, extra = {}) => ({
  id, status: "published", updated_at: "2026-09-01T00:00:00Z",
  listing: { title, price: 22.99, quantity: 1, currency: "USD",
             condition: "USED_EXCELLENT", brand: "Johnny O",
             ebay_listing_id: `11${id}`, source: "ebay", ...extra },
});

let host;
let root;
let app;
let state;

function server() {
  return (url, opts = {}) => {
    const path = String(url);
    const method = (opts.method || "GET").toUpperCase();
    const edit = path.match(/^\/api\/listings\/([^/]+)\/quick-edit$/);
    if (edit && method === "POST") {
      const body = JSON.parse(opts.body || "{}");
      state.sent.push({ id: edit[1], body });
      return json(state.answer(edit[1], body));
    }
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: state.listings });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({});
  };
}

// The server's answer to a save that landed: the record, with the change.
function landed(id, body) {
  state.listings = state.listings.map((it) => (it.id === id
    ? { ...it, listing: { ...it.listing, ...body } } : it));
  const rec = state.listings.find((it) => it.id === id);
  return { ok: true, saved: true, listing: rec.listing, status: rec.status,
           pushed: ["ebay"], results: { ebay: { ok: true } },
           message: "Your eBay listing has been updated." };
}

function Probe() {
  const value = useApp();
  useEffect(() => { app = value; });
  return null;
}

async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function mount(listings, { layout, tab } = {}) {
  state = { listings, sent: [], answer: landed };
  vi.stubGlobal("fetch", vi.fn(server()));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Probe />
          <ListingsView />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await settle();
  if (layout) await act(async () => { app.setListingsLayout(layout); });
  if (tab) await act(async () => { app.setListingsTab(tab); });
}

const toggle = (title) => host.querySelector(`button[aria-label="Quick edit: ${title}"]`);
const panel = (title) => host.querySelector(`form[aria-label="Quick edit: ${title}"]`);
const field = (form, label) => [...form.querySelectorAll("label")]
  .find((l) => l.textContent.trim().startsWith(label))
  ?.querySelector("input, textarea, select");
const buttonIn = (form, label) => [...form.querySelectorAll("button")]
  .find((b) => (b.textContent || "").trim() === label);

async function type(box, text) {
  const proto = box.tagName === "TEXTAREA"
    ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  await act(async () => {
    setter.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function click(el) {
  await act(async () => { el.click(); });
  await settle();
}

describe("quick edit on the Manage grid", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => {
    await act(async () => { root.unmount(); });
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("puts a collapsed toggle on every live card, and nothing more", async () => {
    await mount([live("a1", "Golf Polo"), live("a2", "Picture Frame")]);

    for (const title of ["Golf Polo", "Picture Frame"]) {
      expect(toggle(title), `no toggle on "${title}"`).toBeTruthy();
      expect(toggle(title).getAttribute("aria-expanded")).toBe("false");
      expect(panel(title)).toBeNull();
    }
    // Collapsed means collapsed: no form fields on the grid at all.
    expect(host.querySelectorAll("form, input, textarea, select").length).toBe(0);
  });

  it("opens the fields under the card, with the listing's own values", async () => {
    await mount([live("a1", "Golf Polo")]);

    await click(toggle("Golf Polo"));

    const form = panel("Golf Polo");
    expect(form).toBeTruthy();
    expect(toggle("Golf Polo").getAttribute("aria-expanded")).toBe("true");
    expect(toggle("Golf Polo").getAttribute("aria-controls")).toBe(form.id);
    expect(field(form, "Title").value).toBe("Golf Polo");
    expect(field(form, "Price").value).toBe("22.99");
    expect(field(form, "Available").value).toBe("1");
    expect(field(form, "Brand").value).toBe("Johnny O");
    expect(field(form, "You paid").value).toBe("");
    expect(buttonIn(form, "Update eBay")).toBeTruthy();
  });

  it("sends only what was touched, and the card shows the saved listing", async () => {
    await mount([live("a1", "Golf Polo")]);
    await click(toggle("Golf Polo"));
    const form = panel("Golf Polo");

    await type(field(form, "Title"), "Johnny O Golf Polo Shirt Mens L");
    await type(field(form, "Price"), "19.99");
    await click(buttonIn(form, "Update eBay"));

    expect(state.sent).toEqual([{ id: "a1",
      body: { title: "Johnny O Golf Polo Shirt Mens L", price: 19.99 } }]);
    // Closed on success, and the card carries the answer.
    expect(panel("Johnny O Golf Polo Shirt Mens L")).toBeNull();
    expect(host.textContent).toContain("Johnny O Golf Polo Shirt Mens L");
    expect(host.textContent).toContain("$19.99");
    expect(document.body.textContent).toContain("Your eBay listing has been updated.");
  });

  it("does not send a field typed back to the value it already had", async () => {
    await mount([live("a1", "Golf Polo")]);
    await click(toggle("Golf Polo"));
    const form = panel("Golf Polo");

    await type(field(form, "Price"), "23");
    await type(field(form, "Price"), "22.99");

    const save = buttonIn(form, "Update eBay");
    expect(save.disabled).toBe(true);
    await click(save);
    expect(state.sent).toEqual([]);
  });

  it("keeps the panel open on a refusal, and the card on what eBay still has", async () => {
    await mount([live("a1", "Golf Polo")]);
    state.answer = (id) => ({
      ok: false, saved: false,
      listing: state.listings.find((it) => it.id === id).listing,
      status: "published", pushed: ["ebay"],
      message: "The price is below the category minimum.",
      results: { ebay: { ok: false, message: "The price is below the category minimum.",
        issues: [{ target: "price", level: "error",
                   title: "eBay won't take that price" }] } },
    });
    await click(toggle("Golf Polo"));
    const form = panel("Golf Polo");

    await type(field(form, "Price"), "0.5");
    await click(buttonIn(form, "Update eBay"));

    const open = panel("Golf Polo");
    expect(open, "a refused change closed the panel").toBeTruthy();
    const alert = open.querySelector("[role=alert]");
    expect(alert.textContent).toContain("eBay won't take that price");
    expect(alert.textContent).toContain("Nothing was changed.");
    // What was typed is still there to fix; the card still shows the price
    // buyers see.
    expect(field(open, "Price").value).toBe("0.5");
    expect(host.textContent).toContain("$22.99");
  });

  it("says so in the panel when the server refuses a value", async () => {
    await mount([live("a1", "Golf Polo")]);
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => (String(url).endsWith("/quick-edit")
      ? Promise.resolve({
        ok: false, status: 400,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({ detail: "Connect eBay first — this listing is live there." }),
        text: () => Promise.resolve("{}"),
      })
      : server()(url, opts))));
    await click(toggle("Golf Polo"));
    const form = panel("Golf Polo");

    await type(field(form, "Brand"), "Johnnie-O");
    await click(buttonIn(form, "Update eBay"));

    expect(panel("Golf Polo").querySelector("[role=alert]").textContent)
      .toContain("Connect eBay first");
  });

  it("discards what was typed on Cancel", async () => {
    await mount([live("a1", "Golf Polo")]);
    await click(toggle("Golf Polo"));
    await type(field(panel("Golf Polo"), "Title"), "Something else");

    await click(buttonIn(panel("Golf Polo"), "Cancel"));
    expect(panel("Golf Polo")).toBeNull();

    await click(toggle("Golf Polo"));
    expect(field(panel("Golf Polo"), "Title").value).toBe("Golf Polo");
    expect(state.sent).toEqual([]);
  });

  it("offers no price or stock box on a live auction", async () => {
    // A plain auction has no Buy It Now, its opening bid can't move once it
    // is live, and an auction sells exactly one item.
    await mount([live("a1", "Woodcut Print", { listing_format: "AUCTION", price: null,
                                               auction_start_price: 110 })]);
    await click(toggle("Woodcut Print"));
    const form = panel("Woodcut Print");

    expect(field(form, "Title")).toBeTruthy();
    expect(field(form, "Price")).toBeUndefined();
    expect(field(form, "Buy It Now")).toBeUndefined();
    expect(field(form, "Available")).toBeUndefined();
  });

  it("leaves a sold listing to open as the archive it is", async () => {
    await mount([
      { id: "s1", status: "sold", updated_at: "2026-09-01T00:00:00Z",
        listing: { title: "Sold Mug", price: 12, sold_price: 12 } },
      { id: "e1", status: "ended", updated_at: "2026-09-01T00:00:00Z",
        listing: { title: "Ended Mug", price: 12 } },
    ], { tab: "inactive" });

    expect(host.textContent).toContain("Sold Mug");
    expect(toggle("Sold Mug")).toBeNull();
    expect(toggle("Ended Mug")).toBeNull();
  });

  it("saves a draft without the word eBay on the button, and leaves its price to the card", async () => {
    await mount([{ id: "d1", status: "draft", updated_at: "2026-09-01T00:00:00Z",
                   listing: { title: "Draft Vase", price: 30, quantity: 1 } }],
    { tab: "all" });
    await click(toggle("Draft Vase"));
    const form = panel("Draft Vase");

    // The draft card's own price control (format-aware, with comps) is the
    // one price box under it.
    expect(field(form, "Price")).toBeUndefined();
    await type(field(form, "Quantity"), "3");
    await click(buttonIn(form, "Save"));

    expect(state.sent).toEqual([{ id: "d1", body: { quantity: 3 } }]);
  });

  it("edits a row in place in the list layout", async () => {
    await mount([live("a1", "Golf Polo"), live("a2", "Picture Frame")], { layout: "list" });

    const t = toggle("Picture Frame");
    expect(t.textContent).toContain("Quick edit");
    await click(t);
    const form = panel("Picture Frame");
    expect(form).toBeTruthy();
    expect(panel("Golf Polo")).toBeNull();

    await type(field(form, "Available"), "4");
    await click(buttonIn(form, "Update eBay"));
    expect(state.sent).toEqual([{ id: "a2", body: { quantity: 4 } }]);
  });
});
