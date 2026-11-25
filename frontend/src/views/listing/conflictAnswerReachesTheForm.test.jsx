/* "Keep eBay's" has to reach the form, not only the record.
 *
 * The banner's answer is written server-side and the session is handed the
 * listing it left behind — but the editor's form is seeded once, when the
 * listing opens, and every full save lays the form over the session. So the
 * title box went on showing the seller's own version after they chose
 * eBay's, and the next Update Live Listing saved that version back, marked
 * it dirty and revised it onto eBay: the Seller Hub fix the seller had just
 * chosen to keep, overwritten by the answer they turned down. The stale
 * conflict map rode along too, and with nothing stored to win over it the
 * question they had answered came back.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MotionGlobalConfig } from "framer-motion";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { NewListing } from "@/views/NewListing";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function ok(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

let app;
function Probe() {
  const a = useApp();
  useEffect(() => { app = a; });
  return null;
}

let root;
let host;
let calls;
let skipping;

const LIVE = {
  images: ["img_000.jpg"], title: "My title", category_id: "15709",
  condition: "USED_EXCELLENT", price: 48, quantity: 1,
  package_weight_lb: 2, package_weight_oz: 0, ebay_listing_id: "1234567890",
  conflicts: { title: { local: "My title", remote: "Seller Hub title" } },
};
const RESOLVED = { ...LIVE, title: "Seller Hub title", conflicts: {} };

async function mountLive() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Probe /><NewListing /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => {
    app.setSession({
      sessionId: "s1", listing: { ...LIVE }, status: "published",
      conflicts: [{ field: "title", label: "Title", mine: "My title", ebay: "Seller Hub title" }],
    });
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const titleInput = () => host.querySelector('input[maxlength="80"]');
const byLabel = (label) => host.querySelector(`button[aria-label="${label}"]`);
const byText = (text) => [...host.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);

beforeEach(() => {
  skipping = MotionGlobalConfig.skipAnimations;
  MotionGlobalConfig.skipAnimations = true;
  localStorage.clear();
  calls = [];
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    const path = String(url);
    const body = typeof opts.body === "string" ? JSON.parse(opts.body) : null;
    calls.push({ path, method: opts.method || "GET", body });
    if (path === "/api/listings/s1/resolve-conflict") {
      return ok({ ok: true, listing: RESOLVED, conflicts: [], message: "Kept eBay's title." });
    }
    return ok({});
  }));
});

afterEach(async () => {
  MotionGlobalConfig.skipAnimations = skipping;
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe("answering a conflict in the editor", () => {
  it("shows eBay's value once the seller keeps it, and saves that value", async () => {
    await mountLive();
    expect(titleInput().value).toBe("My title");

    await act(async () => { byLabel("Keep eBay’s title").click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(titleInput().value).toBe("Seller Hub title");

    await act(async () => { byText("Update Live Listing").click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    const save = calls.find((c) => c.path === "/api/save/s1");
    expect(save.body.title).toBe("Seller Hub title");
    // The answered question is not sent back to be asked again.
    expect(save.body.conflicts).toEqual({});
  });

  it("leaves the seller's own value alone when they keep theirs", async () => {
    await mountLive();
    await act(async () => { byLabel("Keep your title").click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(titleInput().value).toBe("My title");
  });
});
