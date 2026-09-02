/* A batch the seller can get out of.
 *
 * Bulk had no off switch: once the photos were in, the only way off the
 * progress bar was to let the whole pile finish — and a batch that had
 * stopped moving left the seller watching a bar that would never fill while
 * the AI kept spending on their account.
 *
 * So the queue carries Stop, and stopping is not losing: the items already
 * drafted are saved listings and the screen says so, because a stop that
 * reads like a discard is one nobody dares press.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { BulkQueue } from "@/views/listing/BulkMode";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
};

const RUNNING = {
  id: "job1", done: false, phase: "identifying", current: 2,
  total_items: 6, total_photos: 12, items: [],
};

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function server(calls, status) {
  return (url, opts = {}) => {
    const path = String(url);
    if (path.startsWith("/api/bulk/cancel/")) {
      calls.push(path);
      return json({ ok: true, stopped: true, already_finished: false });
    }
    if (path.startsWith("/api/bulk/status/")) return json(status);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: [] });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

async function mount(calls = [], status = RUNNING) {
  vi.stubGlobal("fetch", vi.fn(server(calls, status)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <BulkQueue jobId="job1" onExit={() => {}} onSettled={() => {}} />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, text: () => document.body.textContent || "" };
}

function byText(label) {
  return [...document.body.querySelectorAll("button")]
    .find((b) => (b.textContent || "").trim() === label);
}

describe("stopping a running batch", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("offers a way out while the batch is running", async () => {
    const { root } = await mount();
    expect(byText("Stop batch")).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  it("asks the server to stop, without a second prompt", async () => {
    // No confirm step: nothing is deleted (drafted items are saved listings),
    // and a seller escaping a stuck batch should escape on the first tap.
    const calls = [];
    const { root } = await mount(calls);
    await act(async () => { byText("Stop batch").click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(calls).toEqual(["/api/bulk/cancel/job1"]);
    // Latched until the poll brings the finished job back, so the tap can't
    // be repeated and doesn't look ignored for the second and a half between.
    expect(byText("Stopping…")).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  it("says what a stopped batch kept, not that it was thrown away", async () => {
    const items = [
      { session_id: "s1", status: "draft", title: "Nike hoodie",
        listing: { title: "Nike hoodie", price: 20, images: ["img_000.jpg"] } },
      { session_id: "s2", status: "draft", title: "Canon AE-1",
        listing: { title: "Canon AE-1", price: 90, images: ["img_000.jpg"] } },
    ];
    const { root, text } = await mount([], {
      id: "job1", done: true, cancelled: true, phase: "stopped",
      current: 2, total_items: 6, items,
    });
    expect(text()).toContain("You stopped this batch");
    expect(text()).toContain("2 items it finished are saved in Drafts");
    // And the progress bar is gone — the batch is over.
    expect(byText("Stop batch")).toBeFalsy();
    await act(async () => { root.unmount(); });
  });
});
