/* patchListing changes the store only when it changes a card.
 *
 * It is called once per photo as the device queue cuts a pile -- often for a
 * listing that is not in the loaded page at all -- and its "nothing changed"
 * check compared the result of a .map() to the array it was mapped from,
 * which is never equal. So every call was a new listings state, and every
 * screen reading the app context re-rendered, for a card nobody could see.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function reply(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status, headers: { get: () => null },
    json: () => Promise.resolve(body),
  });
}

function respond(path) {
  if (path === "/api/listings") {
    return reply({ authed: true, db: { configured: true },
                   listings: [{ id: "l1", status: "draft", updated_at: "1" }] });
  }
  if (path.startsWith("/api/auth/me")) return reply({ user: { id: 7 } });
  return reply({ detail: "Not found" }, 404);
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

beforeEach(() => { vi.stubGlobal("fetch", vi.fn((url) => respond(String(url)))); });
afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

describe("patchListing", () => {
  it("leaves the store as it was for a listing that is not loaded", async () => {
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
    const before = app.listingsState;
    expect(before.items).toHaveLength(1);

    await act(async () => { app.patchListing("not-loaded", { updated_at: "2" }); });
    expect(app.listingsState).toBe(before);

    await act(async () => { app.patchListing("l1", { updated_at: "2" }); });
    expect(app.listingsState.items[0].updated_at).toBe("2");
    await act(async () => { root.unmount(); });
  });
});
