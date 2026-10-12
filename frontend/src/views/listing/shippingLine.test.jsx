/* Shipping is the weight row and one sentence.
 *
 * The sentence names the policy the listing will go out with and the
 * returns and payment policies in effect; `change` swaps the policy picker
 * in under it, and `done` or Escape swaps it back out with focus returned
 * to `change` (LISTING_REDESIGN.md, "Shipping" and "Keyboard").
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { ShippingCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const ROUTES = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true },
  "/api/ebay/policies": {
    policies: {
      fulfillment: [
        { id: "fp1", name: "USPS Ground Advantage", summary: "1 business day", services: ["USPS_GroundAdvantage"] },
        { id: "fp2", name: "UPS Ground", summary: "", services: ["UPS_Ground"] },
      ],
      return: [{ id: "rp1", name: "30-day" }],
      payment: [{ id: "pp1", name: "Managed" }],
    },
    selected: { fulfillment_policy_id: "fp1", return_policy_id: "rp1", payment_policy_id: "pp1" },
  },
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

function stub(over = {}) {
  return {
    fixLevel: () => undefined,
    fixTarget: null,
    form: { package_weight_lb: 1, package_weight_oz: 4, fulfillment_policy_id: "", ...over },
    completion: { shipping: "done" },
    set: vi.fn(),
  };
}

let root;
let host;

async function mount(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<ToastProvider><AppProvider><ShippingCard w={w} /></AppProvider></ToastProvider>);
  });
  // Let the status and policies answers land.
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const line = () => host.querySelector("[data-shipping-line]");
const change = () => [...line().querySelectorAll("button")].find((b) => /^(change|done)$/.test(b.textContent.trim()));

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn((url) => {
    const path = String(url).split("?")[0];
    return json(ROUTES[path] ?? {});
  }));
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe("the shipping line", () => {
  it("names the policies in effect in one sentence, with the account default marked", async () => {
    await mount(stub());
    const text = line().textContent;
    expect(text).toContain("Ships with USPS Ground Advantage");
    expect(text).toContain("account default");
    expect(text).toContain("30-day returns");
    expect(text).toContain("Managed payment");
    expect(host.querySelector("select")).toBeNull();
  });

  it("swaps the picker in on change, and out again on done", async () => {
    await mount(stub());
    await act(async () => { change().click(); });
    expect(change().getAttribute("aria-expanded")).toBe("true");
    expect(host.querySelector("select")).toBeTruthy();
    expect(host.textContent).toContain("Edit your policies in Settings");
    await act(async () => { change().click(); });
    expect(host.querySelector("select")).toBeNull();
  });

  it("closes on Escape from inside the picker, with focus back on change", async () => {
    await mount(stub());
    await act(async () => { change().click(); });
    const select = host.querySelector("select");
    await act(async () => {
      select.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    });
    expect(host.querySelector("select")).toBeNull();
    expect(change().textContent.trim()).toBe("change");
    expect(document.activeElement).toBe(change());
  });
});
