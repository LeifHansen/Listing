/* A 404 is read off the response's status, never out of its sentence.
 *
 * api() words an error from the server's own `detail` — "Unknown bulk job.",
 * "Listing not found" — and keeps the number on `err.status`. Every FastAPI
 * 404 carries a detail, so the message never holds the status. Four places
 * decided "the server has no record of this" by looking for "(404)" in the
 * message, a string that could not be there, so each branch was dead:
 *
 *   - the batch queue never reached its "the server restarted" ending; it
 *     polled a forgotten job as a run of blips and ended on "Lost the
 *     connection", which is not what happened;
 *   - dismissing an item the AI could not identify — whose 404 is the
 *     EXPECTED answer, a failed identify having saved nothing — showed
 *     "Couldn't dismiss that: Listing not found" and left the card up;
 *   - and the store's batch heartbeat and import watch, the same test in
 *     store.jsx, never settled a job the server had forgotten.
 *
 * The fakes below answer the way the server does: a 404 WITH a detail.
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
  "/api/ebay/policies": { policies: [] },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
  "/api/listings": { authed: true, db: { configured: true, connected: true },
                     listings: [] },
};

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function server(routes) {
  return (url, opts = {}) => {
    const path = String(url);
    const method = (opts.method || "GET").toUpperCase();
    for (const [match, answer] of routes) {
      if (match(path, method)) return answer();
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
  };
}

async function mount(routes, onSettled = () => {}) {
  vi.stubGlobal("fetch", vi.fn(server(routes)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <BulkQueue jobId="job1" onExit={() => {}} onSettled={onSettled} />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  return { root, text: () => document.body.textContent || "" };
}

describe("a forgotten job is a status, not a sentence", () => {
  beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("ends the queue on the restart it was, after a second look", async () => {
    const onSettled = vi.fn();
    const { root, text } = await mount([
      [(p) => p.startsWith("/api/bulk/status/"),
       () => json({ detail: "Unknown bulk job." }, 404)],
    ], onSettled);
    // One 404 is not believed: an auth blip mid-batch can do that.
    expect(onSettled).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(onSettled).toHaveBeenCalled();
    expect(text()).toContain("This batch stopped early");
    expect(text()).not.toContain("Lost the connection");
    await act(async () => root.unmount());
  });

  it("dismisses an unidentified item whose record was never saved", async () => {
    const deletes = [];
    const { root, text } = await mount([
      [(p) => p.startsWith("/api/bulk/status/"),
       () => json({ done: true, items: [
         { session_id: "lost1", status: "error",
           error: "Couldn't tell what this was." }] })],
      [(p, m) => m === "DELETE" && p.startsWith("/api/listings/lost1"),
       () => { deletes.push(1); return json({ detail: "Listing not found" }, 404); }],
    ]);
    const button = document.querySelector('[aria-label="Dismiss this item"]');
    expect(button).toBeTruthy();
    await act(async () => { button.click(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(deletes).toHaveLength(1);
    expect(text()).not.toContain("Couldn't dismiss that");
    expect(document.querySelector('[aria-label="Dismiss this item"]')).toBeNull();
    await act(async () => root.unmount());
  });
});
