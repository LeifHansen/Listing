/* Coming back to the tab polls the batch once, not twice over.
 *
 * The queue re-polls the moment the tab is visible again, because browsers
 * throttle a hidden tab's timers. A browser says so twice -- visibilitychange
 * and then focus, back to back (store.jsx's metrics load notes the same
 * pair) -- and each started a poll of its own while the first was still out.
 * Each of those scheduled its own next poll, so every return to the tab
 * added another loop asking for the same status.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { BulkQueue } from "./BulkMode";

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

let root;
let host;
let statusPolls;

async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

async function advance(ms) {
  // In poll-sized steps, letting each answer land before the next tick.
  for (let t = 0; t < ms; t += 500) {
    await act(async () => { vi.advanceTimersByTime(500); await flush(); });
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  statusPolls = 0;
  vi.stubGlobal("fetch", vi.fn((url) => {
    const path = String(url);
    if (path.startsWith("/api/bulk/status/")) { statusPolls += 1; return json(RUNNING); }
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true }, listings: [] });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  }));
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  host.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("the batch's status poll", () => {
  it("is still one loop after the tab comes back", async () => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => {
      root.render(
        <ToastProvider><AppProvider>
          <BulkQueue jobId="job1" onExit={() => {}} onSettled={() => {}} />
        </AppProvider></ToastProvider>,
      );
      await flush();
    });
    await advance(3000);

    let start = statusPolls;
    await advance(15000);
    const before = statusPolls - start;

    // What a browser sends on the way back to the tab: both, back to back.
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
      await flush();
    });
    await advance(3000);

    start = statusPolls;
    await advance(15000);
    expect(statusPolls - start).toBeLessThanOrEqual(before + 1);
  });
});
