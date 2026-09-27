/* A shipping quote belongs to the order it was asked for.
 *
 * A quote is EasyPost's shipment for one order's buyer. "Get rates" can take
 * seconds, and nothing stopped a seller from going back to the order list and
 * picking another order while it ran. The first order's answer then landed
 * under the second, and "Buy label" sent the second order's id with the first
 * order's shipment — the server takes both as given — so the label was bought
 * to one buyer's address and recorded, and marked shipped on eBay, as the
 * other's.
 *
 * Rendered with react-dom + act, like shippingDialogExplainsAnEmptyPile.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShippingDialog } from "@/components/ShippingDialog";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const app = vi.hoisted(() => ({ current: {} }));
vi.mock("@/store", () => ({ useApp: () => app.current }));

// Rates answers are held until the test releases them, one per request.
const served = vi.hoisted(() => ({ routes: {}, posted: [], pendingRates: [] }));
vi.mock("@/lib/api", () => ({
  api: (path) => {
    const key = Object.keys(served.routes).find((k) => path.startsWith(k));
    return key ? Promise.resolve(served.routes[key]) : Promise.reject(new Error("no route " + path));
  },
  postJson: (path, body) => {
    served.posted.push({ path, body });
    if (path === "/api/easypost/rates") {
      return new Promise((resolve) => served.pendingRates.push({ body, resolve }));
    }
    if (path === "/api/easypost/label") {
      return Promise.resolve({ status: "bought", order_id: body.order_id,
                               tracking_number: "TRK1", ebay_marked: true });
    }
    return Promise.reject(new Error("no route " + path));
  },
}));
const toaster = vi.hoisted(() => ({
  toast: vi.fn(), confirm: vi.fn(() => Promise.resolve(true)),
}));
vi.mock("@/components/ui/Toaster", () => ({ useToast: () => toaster }));
vi.mock("@/components/ui/Dialog", () => ({
  Dialog: ({ open, children }) => (open ? <div data-dialog>{children}</div> : null),
}));

let root = null;
let host = null;

const order = (id, title, name) => ({
  order_id: id, total: "20.00", currency: "USD",
  line_items: [{ title }],
  ship_to: { name, address1: "1 Main St", city: "Columbus", state: "OH",
             postal_code: "43004", country: "US" },
  labels: [],
});
const A = order("oA", "Vintage lamp", "Avery");
const B = order("oB", "Wool coat", "Blake");

const quoteFor = (tag) => ({
  shipment_id: `shp_${tag}`,
  rates: [{ rate_id: `rate_${tag}`, carrier: `Carrier${tag}`, service: "Ground",
            cost: "7.10", currency: "USD" }],
  messages: [],
});

async function mount() {
  served.routes["/api/ebay/orders"] = { orders: [A, B], total: 2, partial: false };
  served.routes["/api/prefs"] = { prefs: {} };
  app.current = {
    shipping: {},
    closeShipping: vi.fn(),
    loadNotifications: vi.fn(),
    setView: vi.fn(),
    easypost: { connected: true, test: false, key_hint: "abcd", loaded: true },
  };
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<ShippingDialog />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const button = (t) => [...host.querySelectorAll("button")]
  .find((b) => b.textContent.trim().startsWith(t));
const click = async (el) => { await act(async () => { el.click(); }); };
const release = async (i, body) => {
  await act(async () => { served.pendingRates[i].resolve(body); });
};

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  served.routes = {};
  served.posted = [];
  served.pendingRates = [];
  toaster.toast.mockClear();
});

describe("a quote is for the order it was asked for", () => {
  it("drops an answer for an order the seller has left", async () => {
    await mount();
    await click(button("Vintage lamp"));
    await click(button("Get rates"));
    expect(served.pendingRates[0].body.order_id).toBe("oA");

    // Back to the list and on to the other order while A's quote is out.
    await click(button("← All orders"));
    await click(button("Wool coat"));
    await release(0, quoteFor("A"));

    expect(host.textContent).not.toContain("CarrierA");
    expect(button("Buy label")).toBeUndefined();
  });

  it("buys the label with the shipment quoted for THIS order", async () => {
    await mount();
    await click(button("Vintage lamp"));
    await click(button("Get rates"));
    await click(button("← All orders"));
    await click(button("Wool coat"));
    await click(button("Get rates"));
    // B's answer first, then A's stale one arrives late.
    await release(1, quoteFor("B"));
    await release(0, quoteFor("A"));

    expect(host.textContent).toContain("CarrierB");
    expect(host.textContent).not.toContain("CarrierA");
    await click(button("Buy label"));
    const buy = served.posted.find((p) => p.path === "/api/easypost/label");
    expect(buy.body).toMatchObject({ order_id: "oB", shipment_id: "shp_B",
                                     rate_id: "rate_B" });
  });
});
