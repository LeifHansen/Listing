/* The editor's header: a back arrow, the title, and one ⋯ menu.
 *
 * It used to carry five buttons (My drafts, Start over, Delete, Exit, Back
 * to batch), with four more in the bar. The once-a-month actions are a menu
 * now (LISTING_REDESIGN.md, "Header"), and the ones that depend on the
 * listing's state come and go: no Delete while the listing is live (ending
 * it first is the safe order), View on eBay only once there is something on
 * eBay to view, and Save to eBay drafts only for a draft.
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

const DRAFT = {
  images: ["img_000.jpg"], title: "Nike Air Max 90", category_id: "15709",
  condition: "USED_EXCELLENT", price: 48, quantity: 1,
  package_weight_lb: 2, package_weight_oz: 0,
};

async function mountEditor(listing, status = "draft") {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Probe /><NewListing /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { app.setSession({ sessionId: "s1", listing, status }); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const header = () => host.querySelector("h1").closest("div").parentElement;
const headerButtons = () => [...header().querySelectorAll("button")]
  .map((b) => (b.textContent || "").trim() || b.getAttribute("aria-label"));
const menuItems = () => [...host.querySelectorAll('[role="menuitem"]')]
  .map((b) => b.textContent.trim());
async function openMenu() {
  await act(async () => { host.querySelector('button[aria-haspopup="menu"]').click(); });
}

let skipping;
beforeEach(() => {
  skipping = MotionGlobalConfig.skipAnimations;
  MotionGlobalConfig.skipAnimations = true;
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(() => ok({})));
});

afterEach(async () => {
  MotionGlobalConfig.skipAnimations = skipping;
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe("the editor header", () => {
  it("is a back arrow, the title and one menu", async () => {
    await mountEditor(DRAFT);
    expect(host.querySelector("h1").textContent).toBe("Nike Air Max 90");
    expect(headerButtons()).toEqual(["Close this listing", "More actions"]);
    // None of the old row survives anywhere on the page.
    const all = [...host.querySelectorAll("button")].map((b) => b.textContent.trim());
    for (const gone of ["Start over", "My drafts", "Exit", "Save Draft"]) {
      expect(all, gone).not.toContain(gone);
    }
  });

  it("offers a draft its once-a-month actions, Delete last", async () => {
    await mountEditor(DRAFT);
    await openMenu();
    // Publish heads the list for its shortcut (Ctrl/⌘+Enter), which the
    // menu is the one place to learn.
    expect(menuItems().map((t) => t.replace(/[⌘↩]|Ctrl\+/g, "").trim())).toEqual([
      "Publish Live", "New listing", "Check with eBay", "Save to eBay drafts", "Delete listing",
    ]);
    const publish = host.querySelector('[role="menuitem"]');
    expect(publish.textContent).toMatch(/⌘↩|Ctrl\+↩/);
  });

  it("never offers Delete on a live listing, and does offer View on eBay", async () => {
    await mountEditor({ ...DRAFT, ebay_listing_id: "1234567890" }, "published");
    await openMenu();
    expect(menuItems().map((t) => t.replace(/[⌘↩]|Ctrl\+/g, "").trim())).toEqual([
      "Update Live Listing", "New listing", "Check with eBay", "View on eBay",
    ]);
  });

  it("keeps Back to batch beside the menu while a batch is running", async () => {
    await mountEditor(DRAFT);
    await act(async () => { app.startBulk("job-1"); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(headerButtons()).toContain("Back to batch");
  });

  it("closes onto the screen it was opened from, without a question, on a saved draft", async () => {
    await mountEditor(DRAFT);
    await act(async () => { host.querySelector('button[aria-label="Close this listing"]').click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(app.session).toBeNull();
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  });
});
