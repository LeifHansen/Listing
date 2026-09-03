/* The finished-batch banner retires itself once there is nothing to review.
 *
 * "Your bulk batch finished. Every item is saved to Drafts — tap to review
 * the results." stayed on every screen after a seller had listed the whole
 * batch, until they found the X. A batch is reviewed when its drafts are
 * gone: listed, sold, ended, put in inventory. A batch whose items are still
 * drafts keeps its banner; so does one that drafted nothing that went
 * anywhere, because the queue behind it is the only place its errors show.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function server(listings) {
  const table = {
    "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
    "/api/health": { anthropic_configured: true, ebay_configured: false },
    "/api/ebay/status": { connected: false },
    "/api/listings": { authed: true, db: { configured: true, connected: true },
                       listings },
    "/api/notifications": { notifications: [], unread: 0 },
    "/api/marketplaces": { marketplaces: [] },
    "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
    "/api/insights": { recommendations: [] },
  };
  return (url) => {
    const path = String(url);
    const key = Object.keys(table).find((k) => path.startsWith(k));
    const body = key ? table[key] : null;
    return Promise.resolve({
      ok: !!body, status: body ? 200 : 404,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve(body || { detail: "Not found" }),
      text: () => Promise.resolve(JSON.stringify(body || { detail: "Not found" })),
    });
  };
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount(listings) {
  vi.stubGlobal("fetch", vi.fn(server(listings)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Probe onValue={(a) => { app = a; }} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, app: () => app };
}

async function settle(app, ids) {
  await act(async () => { app().startBulk("job-1"); });
  await act(async () => { app().bulkSettled(ids); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

describe("the finished-batch banner", () => {
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); document.body.innerHTML = ""; });

  it("retires itself once every draft the batch made has been listed", async () => {
    const { root, app } = await mount([
      { id: "s1", title: "Levi's 501", status: "published" },
      { id: "s2", title: "Levi's 505", status: "sold" },
    ]);
    await settle(app, ["s1", "s2"]);
    expect(app().activeBulk).toBeNull();
    await act(async () => { root.unmount(); });
  });

  it("stays while any of them is still a draft", async () => {
    const { root, app } = await mount([
      { id: "s1", title: "Levi's 501", status: "published" },
      { id: "s2", title: "Levi's 505", status: "draft" },
    ]);
    await settle(app, ["s1", "s2"]);
    expect(app().activeBulk).toEqual(
      expect.objectContaining({ jobId: "job-1", done: true }));
    await act(async () => { root.unmount(); });
  });

  it("stays when nothing it drafted went anywhere", async () => {
    // Every item errored, or was deleted: the queue is where that shows, and
    // the banner is the way back to it.
    const { root, app } = await mount([]);
    await settle(app, ["s1", "s2"]);
    expect(app().activeBulk).toEqual(expect.objectContaining({ done: true }));
    await act(async () => { root.unmount(); });
  });

  it("keeps its X when the settle carried no ids", async () => {
    const { root, app } = await mount([{ id: "s1", status: "published" }]);
    await settle(app, undefined);
    expect(app().activeBulk).toEqual(expect.objectContaining({ done: true }));
    await act(async () => { app().clearBulk(); });
    expect(app().activeBulk).toBeNull();
    await act(async () => { root.unmount(); });
  });
});
