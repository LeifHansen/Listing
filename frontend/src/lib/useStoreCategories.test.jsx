/* "We couldn't read your eBay Store's categories just now — try again in a
 * moment" has to be true.
 *
 * The shelves are read once and shared through the app store, and the hook
 * only asked while the store held nothing. A failed read is not nothing: it
 * was cached as {checked: false}, so one eBay blip left the editor's picker,
 * the Manage filter and the bulk move saying "try again" for the rest of the
 * session, with nothing that would ever try. Now a reader that mounts onto a
 * failed read asks once more -- opening the panel again IS the retry -- and
 * a reader that is already showing the failure does not loop on it.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { useStoreCategories } from "./useStoreCategories";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let shelvesUp;
let shelfReads;

function reply(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status, headers: { get: () => null },
    json: () => Promise.resolve(body),
  });
}

const ROUTES = {
  "/api/auth/me": { user: { id: 7 } },
  "/api/ebay/status": { connected: true },
  "/api/listings": { authed: true, db: { configured: true }, listings: [] },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
};

function respond(path) {
  if (path.startsWith("/api/ebay/store-categories")) {
    shelfReads += 1;
    return shelvesUp
      ? reply({ store: true, categories: [{ id: "1", name: "Vintage Tees" }] })
      : reply({ detail: "eBay didn't answer." }, 503);
  }
  const key = Object.keys(ROUTES).find((k) => path.startsWith(k));
  return key ? reply(ROUTES[key]) : reply({ detail: "Not found" }, 404);
}

let seen;
function Reader() {
  const shelves = useStoreCategories();
  useEffect(() => { seen = shelves; });
  return null;
}

function Shell({ showReader }) {
  return (
    <ToastProvider>
      <AppProvider>{showReader && <Reader />}</AppProvider>
    </ToastProvider>
  );
}

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  shelvesUp = false;
  shelfReads = 0;
  seen = null;
  vi.stubGlobal("fetch", vi.fn((url) => respond(String(url))));
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("a failed read of the Store's shelves", () => {
  it("is asked again by the next reader, not cached for the session", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<Shell showReader />); });
    await settle();
    await settle();
    expect(seen.checked).toBe(false);
    // Showing the failure does not turn into a loop of reads.
    expect(shelfReads).toBe(1);

    // The seller closes the panel and opens it again a moment later.
    shelvesUp = true;
    await act(async () => { root.render(<Shell showReader={false} />); });
    await act(async () => { root.render(<Shell showReader />); });
    await settle();
    await settle();
    expect(shelfReads).toBe(2);
    expect(seen.checked).toBe(true);
    expect(seen.categories.map((c) => c.name)).toEqual(["Vintage Tees"]);
    await act(async () => { root.unmount(); });
  });
});
