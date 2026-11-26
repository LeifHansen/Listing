/* Signing out has to leave nothing of the last seller for the next one.
 *
 * store.logout.test.jsx pins the caches clearSignedInState resets. Two kinds
 * of leftover got past it:
 *
 *   * caches it did not name. The eBay Store's shelves (storeCategoriesData)
 *     are fetched once and only while empty, so the next seller on the same
 *     tab was offered the last one's shelves -- in the editor's picker, the
 *     Manage filter and "Move to store category…", which would send the last
 *     seller's category ids to revise this one's listings. The Etsy shop's
 *     profiles stayed too, and showed until the next seller's own read landed.
 *
 *   * answers still on their way. A read the last seller started -- their
 *     sales, a listing they tapped open -- landed after the sign-out and put
 *     their data back on screen. Each of those reads already carries a
 *     "newest request wins" counter; signing out has to count as newer.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

// The browser-side downscale needs a real decoder; jsdom has none.
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, downscaleAllForUpload: async (files) => files };
});

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let routes;
// Paths whose answer the test hands over by hand: path prefix -> resolve.
let held;

function reply(body) {
  return Promise.resolve({
    ok: !!body, status: body ? 200 : 404,
    headers: { get: () => null },
    json: () => Promise.resolve(body || { detail: "Not found" }),
  });
}

function respond(path) {
  const hold = Object.keys(held).find((k) => path.startsWith(k));
  if (hold) {
    return new Promise((resolve) => { held[hold] = (body) => resolve(reply(body)); });
  }
  const key = Object.keys(routes).sort((a, b) => b.length - a.length)
    .find((k) => path.startsWith(k));
  return reply(key ? routes[key] : null);
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider><Probe onValue={(v) => { app = v; }} /></AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, get: () => app };
}

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  held = {};
  routes = {
    "/api/auth/me": { user: { id: 7, email: "a@example.com" } },
    "/api/health": { anthropic_configured: true },
    "/api/ebay/status": { connected: true, username: "seller_a" },
    "/api/listings": { authed: true, db: { configured: true }, listings: [] },
    "/api/marketplaces": { marketplaces: [{ key: "ebay", connected: true },
                                          { key: "etsy", connected: true }] },
    "/api/etsy/settings-options": { shipping_profiles: [{ id: "a-ship", name: "Seller A shipping" }] },
    "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
    "/api/auth/logout": { ok: true },
  };
  vi.stubGlobal("fetch", vi.fn((url) => respond(String(url))));
  localStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("the next seller on this tab", () => {
  it("is not offered the last seller's eBay Store shelves", async () => {
    const { get, root } = await mount();
    await act(async () => {
      get().setStoreCategoriesData({ store: true, checked: true,
        categories: [{ id: "1", name: "Seller A shelf" }] });
    });
    await act(async () => { await get().logout(); });
    expect(get().storeCategoriesData).toBe(null);
    await act(async () => { root.unmount(); });
  });

  it("does not see the last seller's Etsy shop while their own loads", async () => {
    const { get, root } = await mount();
    expect(get().etsyOptions.shipping_profiles[0].name).toBe("Seller A shipping");
    // Signed out, nobody has a marketplace connected.
    const connected = routes["/api/marketplaces"];
    routes["/api/marketplaces"] = { marketplaces: [] };
    await act(async () => { await get().logout(); });
    await settle();

    // Seller B signs in with Etsy connected; their shop's read is still out.
    routes["/api/marketplaces"] = connected;
    held["/api/etsy/settings-options"] = null;
    await act(async () => { get().setUser({ id: 8 }); });
    await settle();
    expect(get().connectedMarketplaces.some((m) => m.key === "etsy")).toBe(true);
    expect(get().etsyOptions).toBe(null);
    await act(async () => { root.unmount(); });
  });
});

describe("an answer the last seller was waiting on", () => {
  it("does not put their sales back after they sign out", async () => {
    const { get, root } = await mount();
    held["/api/sale-events"] = null;
    act(() => { get().loadSaleEvents(); });
    await settle();
    await act(async () => { await get().logout(); });

    await act(async () => {
      held["/api/sale-events"]({ events: [{ id: "e1", item_ids: ["1"] }] });
    });
    await settle();
    expect(get().saleEvents.items).toEqual([]);
    expect(get().saleEvents.loaded).toBe(false);
    await act(async () => { root.unmount(); });
  });

  it("does not open their listing over the signed-out app", async () => {
    const { get, root } = await mount();
    held["/api/listings/l1"] = null;
    act(() => { get().openListing("l1"); });
    await settle();
    await act(async () => { await get().logout(); });

    await act(async () => {
      held["/api/listings/l1"]({ id: "l1", status: "draft", listing: { title: "Seller A's lamp" } });
    });
    await settle();
    expect(get().session).toBe(null);
    expect(get().view).toBe("dashboard");
    await act(async () => { root.unmount(); });
  });

  it("does not keep watching their store import, or report it as failed", async () => {
    // The automatic first sync starts on mount; its start is still out.
    held["/api/ebay/import-listings"] = null;
    const { get, root } = await mount();
    expect(get().storeSync.syncing).toBe(true);
    await act(async () => { await get().logout(); });

    await act(async () => { held["/api/ebay/import-listings"]({ job_id: "imp-a" }); });
    // Past the first poll's 2s wait. The job's status route only answers its
    // owner, so watching it from here would end in "The sync stopped".
    await act(async () => { await new Promise((r) => setTimeout(r, 2300)); });
    expect(fetch.mock.calls.some(([u]) => String(u).includes("/api/ebay/import-status")))
      .toBe(false);
    expect(get().storeSync.syncing).toBe(false);
    expect(get().storeSync.error).toBe(null);
    await act(async () => { root.unmount(); });
  }, 10000);

  it("does not turn a bulk upload that finishes after sign-out into a batch", async () => {
    const { get, root } = await mount();
    held["/api/bulk/upload"] = null;
    // Consent for the photos was given; that is not what this is about.
    localStorage.setItem("thryft-ai-consent", "yes");
    const file = new File(["x"], "a.jpg", { type: "image/jpeg" });
    act(() => { get().runBulkUpload([{ file, url: "blob:a" }], false); });
    await settle();
    expect(get().activeBulk).toEqual({ jobId: null });
    await act(async () => { await get().logout(); });

    await act(async () => { held["/api/bulk/upload"]({ job_id: "job-a" }); });
    await settle();
    expect(get().activeBulk).toBe(null);
    expect(get().view).toBe("dashboard");
    expect(localStorage.getItem("thryft-bulk")).toBe(null);
    await act(async () => { root.unmount(); });
  });
});
