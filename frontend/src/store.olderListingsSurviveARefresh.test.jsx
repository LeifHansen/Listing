/* The older listings a seller loaded stay loaded.
 *
 * Past the page cap (3,000) the grid shows the newest page and a "Load older
 * listings" button, which appends the next page. Every refresh then threw
 * those pages away: /api/listings answers the FIRST page, and loadListings
 * replaced `items` with it. A refresh is not rare -- every write invalidates
 * the cache, and coming back to the tab after a minute re-reads it -- so a
 * seller who loaded older listings, rotated a photo on one of them, or looked
 * away to eBay and back, found the listing they were working on gone and the
 * grid back at the top.
 *
 * Now a refresh replaces the first page and keeps the pages behind it. They
 * are not re-read (that would be a page of 3,000 per write); a write to one
 * of them reaches its card through patchListing, and a delete through the
 * delete itself.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let firstPage;
let olderPage;
let held;

const row = (id, status = "published") => ({ id, status, listing: { title: id } });

function reply(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status, headers: { get: () => null },
    json: () => Promise.resolve(body),
  });
}

function respond(path, opts = {}) {
  if (path.startsWith("/api/listings?before=")) {
    const body = { authed: true, db: { configured: true }, ...olderPage };
    if (held.older !== undefined) {
      return new Promise((resolve) => { held.older = () => resolve(reply(body)); });
    }
    return reply(body);
  }
  if (path === "/api/listings") {
    const body = { authed: true, db: { configured: true }, ...firstPage };
    if (held.first !== undefined) {
      return new Promise((resolve) => { held.first = () => resolve(reply(body)); });
    }
    return reply(body);
  }
  if (path.startsWith("/api/listings/") && opts.method === "DELETE") return reply({ ok: true });
  if (path.startsWith("/api/auth/me")) return reply({ user: { id: 7 } });
  if (path.startsWith("/api/tokens")) return reply({ enabled: false, total: 0, packs: [], costs: {} });
  return reply({ detail: "Not found" }, 404);
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider><Probe onValue={(v) => { app = v; }} /></AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, get: () => app };
}

const ids = (app) => app.listingsState.items.map((i) => i.id);
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  held = {};
  firstPage = { listings: [row("new1"), row("new2")], truncated: true, total: 4,
                next_cursor: "c1" };
  olderPage = { listings: [row("old1"), row("old2")], truncated: false, total: 4,
                next_cursor: null };
  vi.stubGlobal("fetch", vi.fn((url, opts) => respond(String(url), opts)));
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("a refresh after Load older listings", () => {
  it("keeps the older page, and the end of the store it reached", async () => {
    const { get, root } = await mount();
    await act(async () => { await get().loadMoreListings(); });
    expect(ids(get())).toEqual(["new1", "new2", "old1", "old2"]);

    // A write somewhere in the store: the first page comes back with a new
    // listing at the top.
    firstPage = { ...firstPage, listings: [row("newest"), row("new1"), row("new2")] };
    await act(async () => { await get().loadListings({ quiet: true }); });
    expect(ids(get())).toEqual(["newest", "new1", "new2", "old1", "old2"]);
    // Still the end of what there is: no "Load older" for a page already shown.
    expect(get().listingsState.nextCursor).toBe(null);
    expect(get().listingsState.truncated).toBe(false);
    await act(async () => { root.unmount(); });
  });

  it("lets the first page's copy of a listing win over the older one", async () => {
    const { get, root } = await mount();
    await act(async () => { await get().loadMoreListings(); });
    // old1 was edited, so it is now among the most recent.
    firstPage = { ...firstPage, listings: [{ ...row("old1"), listing: { title: "edited" } },
                                           row("new1"), row("new2")] };
    await act(async () => { await get().loadListings({ quiet: true }); });
    expect(ids(get())).toEqual(["old1", "new1", "new2", "old2"]);
    expect(get().listingsState.items[0].listing.title).toBe("edited");
    await act(async () => { root.unmount(); });
  });

  it("does not bring back an older listing the seller deleted", async () => {
    const { get, root } = await mount();
    await act(async () => { await get().loadMoreListings(); });
    await act(async () => { await get().deleteListing("old2"); });
    await act(async () => { await get().loadListings({ quiet: true }); });
    expect(ids(get())).toEqual(["new1", "new2", "old1"]);
    await act(async () => { root.unmount(); });
  });

  it("drops the older pages once the first page is the whole store", async () => {
    const { get, root } = await mount();
    await act(async () => { await get().loadMoreListings(); });
    firstPage = { listings: [row("new1"), row("old1")], truncated: false, total: null,
                  next_cursor: null };
    await act(async () => { await get().loadListings({ quiet: true }); });
    expect(ids(get())).toEqual(["new1", "old1"]);
    await act(async () => { root.unmount(); });
  });

  it("keeps an older page that lands after the refresh", async () => {
    const { get, root } = await mount();
    held.older = null;
    act(() => { get().loadMoreListings(); });
    await settle();
    await act(async () => { await get().loadListings({ quiet: true }); });
    // The refresh does not open the door to asking for the same page twice.
    expect(get().listingsState.loadingMore).toBe(true);
    await act(async () => { held.older(); });
    await settle();
    expect(ids(get())).toEqual(["new1", "new2", "old1", "old2"]);
    await act(async () => { root.unmount(); });
  });

  it("keeps an older page that landed while the refresh was out", async () => {
    const { get, root } = await mount();
    held.first = null;
    act(() => { get().loadListings({ quiet: true }); });
    await settle();
    await act(async () => { await get().loadMoreListings(); });
    expect(ids(get())).toEqual(["new1", "new2", "old1", "old2"]);
    await act(async () => { held.first(); });
    await settle();
    expect(ids(get())).toEqual(["new1", "new2", "old1", "old2"]);
    await act(async () => { root.unmount(); });
  });
});
