/* The listing as a buyer meets it, drawn from the form as it is typed. */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { useListingForm } from "./useListingForm";
import { SearchPreview } from "./SearchPreview";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function ok(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function Editor({ onValue }) {
  const app = useApp();
  const w = useListingForm();
  useEffect(() => { onValue({ app, w }); });
  return <SearchPreview w={w} />;
}

let root;
let host;

async function mount(listing, { prefs = {}, policies = null } = {}) {
  vi.stubGlobal("fetch", vi.fn((url) => {
    const path = String(url);
    if (path.startsWith("/api/prefs")) return ok({ prefs });
    if (path.startsWith("/api/ebay/policies")) return ok(policies || { policies: {}, selected: {} });
    return ok({});
  }));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  let value = null;
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Editor onValue={(v) => { value = v; }} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await Promise.resolve(); });
  await act(async () => {
    value.app.setSession({ sessionId: "s1", listing });
    if (policies) value.app.setPoliciesData(policies);
  });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  return () => value;
}

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const text = (sel) => host.querySelector(sel)?.textContent || "";

describe("SearchPreview", () => {
  it("shows the first photo, the title, the condition and the price", async () => {
    await mount({ title: "Nike Air Max 90", condition: "USED_EXCELLENT", price: 64.99,
                  images: ["img_000.jpg", "img_001.jpg"] });
    expect(text("[data-preview-title]")).toBe("Nike Air Max 90");
    expect(text("[data-preview-price]")).toContain("$64.99");
    expect(host.textContent).toContain("Used Excellent");
    expect(host.querySelector("img").getAttribute("src")).toContain("/media/s1/optimized/img_000.jpg");
  });

  it("follows the title as it is typed, and cuts it where eBay does", async () => {
    const get = await mount({ title: "Old", price: 10, images: [] });
    await act(async () => { get().w.set("title", "x".repeat(90)); });
    expect(text("[data-preview-title]")).toHaveLength(80);
    expect(host.textContent).toContain("Cut at 80 characters");
  });

  it("says starting bid for an auction, and never 'or Best Offer' on one", async () => {
    await mount({ title: "T", listing_format: "AUCTION", auction_start_price: 9.99, images: [] },
                { prefs: { allow_offers: 1 } });
    expect(text("[data-preview-price]")).toContain("$9.99");
    expect(text("[data-preview-price]")).toContain("starting bid");
    expect(host.textContent).not.toContain("or Best Offer");
  });

  it("adds the carrier from the policy in effect and the account's offers default", async () => {
    await mount({ title: "T", price: 20, images: [] }, {
      prefs: { allow_offers: 1 },
      policies: { policies: { fulfillment: [{ id: "fp-1", name: "Ground",
                                               summary: "USPS Ground Advantage · eBay International Shipping" }] },
                  selected: { fulfillment_policy_id: "fp-1" } },
    });
    expect(text("[data-preview-extras]")).toBe("USPS Ground Advantage · or Best Offer");
  });

  it("says nothing about shipping or offers when it does not know", async () => {
    await mount({ title: "T", price: 20, images: [] });
    expect(host.querySelector("[data-preview-extras]")).toBeNull();
  });
});
