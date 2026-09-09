/* The shipping dialog, at the two moments that used to leave a seller stuck.
 *
 * "There were no orders to select" was the report: a seller who knew
 * something had sold, looking at "No orders are waiting to ship. 🎉". The
 * server now says which kind of empty it is, and the dialog has to repeat
 * it. And a seller with no EasyPost account must be walked to Settings, not
 * shown a Get rates button that can only fail.
 *
 * Rendered with react-dom + act rather than a testing library, matching
 * MessagesInbox.test.jsx; the repo has no testing-library dependency.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShippingDialog, emptyPileCopy } from "@/components/ShippingDialog";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const app = vi.hoisted(() => ({ current: {} }));
vi.mock("@/store", () => ({ useApp: () => app.current }));

const served = vi.hoisted(() => ({ routes: {}, posted: [] }));
vi.mock("@/lib/api", () => ({
  api: (path) => {
    const key = Object.keys(served.routes).find((k) => path.startsWith(k));
    return key ? Promise.resolve(served.routes[key]) : Promise.reject(new Error("no route " + path));
  },
  postJson: (path, body) => {
    served.posted.push({ path, body });
    const key = Object.keys(served.routes).find((k) => path.startsWith(k));
    return key ? Promise.resolve(served.routes[key]) : Promise.reject(new Error("no route " + path));
  },
}));
// Stable, like the real provider's: the dialog's callbacks depend on these,
// and a fresh function per render would re-run its loading effect forever.
const toaster = vi.hoisted(() => ({
  toast: vi.fn(), confirm: vi.fn(() => Promise.resolve(true)),
}));
vi.mock("@/components/ui/Toaster", () => ({ useToast: () => toaster }));
// The dialog primitive portals and animates; the test wants its children.
vi.mock("@/components/ui/Dialog", () => ({
  Dialog: ({ open, children }) => (open ? <div data-dialog>{children}</div> : null),
}));

let root = null;
let host = null;

async function mount(over = {}) {
  app.current = {
    shipping: {},
    closeShipping: vi.fn(),
    loadNotifications: vi.fn(),
    setView: vi.fn(),
    easypost: { connected: true, test: false, key_hint: "abcd", loaded: true },
    ...over,
  };
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<ShippingDialog />); });
  // Let the order fetch settle.
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return host;
}

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  served.routes = {};
  served.posted = [];
});

const text = () => host.textContent;
const buttonNamed = (t) => [...host.querySelectorAll("button")]
  .find((b) => b.textContent.trim().startsWith(t));

const ORDER = {
  order_id: "o1", total: "20.00", currency: "USD",
  line_items: [{ title: "Vintage lamp" }],
  ship_to: { name: "Jordan", address1: "1 Main St", city: "Columbus", state: "OH", postal_code: "43004", country: "US" },
  labels: [],
};

describe("what an empty pile says", () => {
  it("names the account and the count when everything already shipped", () => {
    expect(emptyPileCopy({ recent_total: 3, ebay_username: "seller_x", env: "production" }))
      .toMatch(/3 orders in the last 90 days on seller_x, all already shipped/);
  });

  it("says when the account has sold nothing at all", () => {
    expect(emptyPileCopy({ recent_total: 0, ebay_username: "seller_x", env: "production" }))
      .toMatch(/no orders at all in the last 90 days on seller_x/);
  });

  it("shouts when the server is on the sandbox", () => {
    expect(emptyPileCopy({ recent_total: 0, ebay_username: "seller_x", env: "sandbox" }))
      .toMatch(/sandbox/);
    expect(emptyPileCopy({ recent_total: 3, env: "production" })).not.toMatch(/sandbox/);
  });

  it("falls back to the plain sentence when eBay gave no count", () => {
    expect(emptyPileCopy({ recent_total: null, ebay_username: "seller_x" }))
      .toBe("No orders are waiting to ship on seller_x.");
  });

  it("reaches the screen", async () => {
    served.routes["/api/ebay/orders"] = {
      orders: [], total: 0, partial: false, recent_total: 5,
      ebay_username: "seller_x", env: "production",
    };
    await mount();
    expect(text()).toMatch(/5 orders in the last 90 days on seller_x/);
  });
});

describe("a seller without EasyPost", () => {
  it("is walked to Settings instead of shown a rate button", async () => {
    served.routes["/api/ebay/orders"] = { orders: [ORDER], total: 1, partial: false };
    await mount({ easypost: { connected: false, test: false, key_hint: "", loaded: true } });
    expect(text()).toMatch(/Connect it once in Settings/);
    expect(buttonNamed("Get rates")).toBeUndefined();
    const connect = buttonNamed("Connect EasyPost");
    expect(connect).toBeTruthy();
    await act(async () => { connect.click(); });
    expect(app.current.closeShipping).toHaveBeenCalled();
    expect(app.current.setView).toHaveBeenCalledWith("settings");
  });

  it("is not told to connect before the shell has asked", async () => {
    served.routes["/api/ebay/orders"] = { orders: [ORDER], total: 1, partial: false };
    await mount({ easypost: { connected: false, test: false, key_hint: "", loaded: false } });
    expect(buttonNamed("Connect EasyPost")).toBeUndefined();
    expect(text()).toMatch(/Checking your EasyPost connection/);
  });
});

describe("a bought label", () => {
  it("opens on the label instead of offering to buy a second one", async () => {
    served.routes["/api/ebay/orders"] = {
      orders: [{ ...ORDER, labels: [{
        status: "bought", tracking_number: "9400111", carrier: "USPS",
        service: "GroundAdvantage", cost: "6.07", currency: "USD",
        label_url: "https://files/label.pdf", ebay_marked: true,
      }] }],
      total: 1, partial: false,
    };
    await mount();
    expect(text()).toMatch(/Label purchased/);
    expect(text()).toMatch(/9400111/);
    expect(buttonNamed("Get rates")).toBeUndefined();
    expect(buttonNamed("Open label PDF")).toBeTruthy();
  });

  it("shows the retry when eBay refused the tracking", async () => {
    served.routes["/api/ebay/orders"] = {
      orders: [{ ...ORDER, labels: [{
        status: "bought", tracking_number: "9400111", carrier: "USPS",
        cost: "6.07", currency: "USD", ebay_marked: false,
        ebay_error: "eBay rejected the tracking number (400)",
      }] }],
      total: 1, partial: false,
    };
    served.routes["/api/ebay/mark-shipped"] = { ok: true };
    await mount();
    expect(text()).toMatch(/eBay couldn't be updated/);
    expect(text()).toMatch(/rejected the tracking/);
    const retry = buttonNamed("Retry marking shipped");
    expect(retry).toBeTruthy();
    await act(async () => { retry.click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(served.posted.find((p) => p.path === "/api/ebay/mark-shipped").body)
      .toEqual({ order_id: "o1", tracking_number: "9400111", carrier: "USPS" });
    expect(text()).toMatch(/Tracking added to the eBay order/);
  });

  it("is shown for a listing whose order has already shipped", async () => {
    served.routes["/api/ebay/orders/for-listing/rec-1"] = {
      order: null,
      labels: [{ status: "bought", tracking_number: "9400111", carrier: "USPS",
                 cost: "6.07", currency: "USD", ebay_marked: true }],
    };
    await mount({ shipping: { listingId: "rec-1" } });
    expect(text()).toMatch(/already shipped/);
    expect(text()).toMatch(/9400111/);
    expect(text()).not.toMatch(/No open order found/);
  });
});
