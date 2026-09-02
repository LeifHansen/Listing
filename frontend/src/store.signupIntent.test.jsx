/* Arriving from the marketing site's "Sign up" has to land in the signup form.
 *
 * The marketing site splits one "Open the app" button into Log in and Sign up,
 * and the only thing carrying that intent across the two origins is
 * `?signup=1`. If the app ignores it, the Sign up button is a lie: it drops a
 * would-be customer on a signed-out app with nothing open, or — worse — on the
 * Log in tab, asking them for a password they have never set.
 *
 * The two failure modes worth pinning are the ones that only show up with
 * timing. A seller who is ALREADY signed in and follows the same link (a
 * bookmark, a shared URL) must never get a signup box thrown over their own
 * dashboard, so the dialog waits for /api/auth/me to answer rather than firing
 * on mount. And the param must not survive in the URL, or a refresh reopens it
 * forever.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const SIGNED_OUT = { "/api/auth/me": { user: null } };
const SIGNED_IN = { "/api/auth/me": { user: { id: 7, email: "seller@example.com" } } };

function respondWith(table) {
  return (url) => {
    const path = String(url);
    const key = Object.keys(table).find((k) => path.startsWith(k));
    const body = key ? table[key] : null;
    return Promise.resolve({
      ok: !!body,
      status: body ? 200 : 404,
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

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Probe onValue={(v) => { app = v; }} />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, host, get: () => app };
}

describe("?signup=1 from the marketing site", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState({}, "", "/?signup=1");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

  it("opens the dialog on the Sign up tab for a signed-out visitor", async () => {
    vi.stubGlobal("fetch", vi.fn(respondWith(SIGNED_OUT)));
    const { get, root, host } = await mount();
    expect(get().authOpen).toBe(true);
    expect(get().authMode).toBe("signup");
    await act(async () => { root.unmount(); });
    host.remove();
  });

  it("drops the param, so a refresh does not reopen it", async () => {
    vi.stubGlobal("fetch", vi.fn(respondWith(SIGNED_OUT)));
    const { root, host } = await mount();
    expect(window.location.search).toBe("");
    await act(async () => { root.unmount(); });
    host.remove();
  });

  it("does not interrupt someone who is already signed in", async () => {
    vi.stubGlobal("fetch", vi.fn(respondWith(SIGNED_IN)));
    const { get, root, host } = await mount();
    expect(get().user).toBeTruthy();
    expect(get().authOpen).toBe(false);
    await act(async () => { root.unmount(); });
    host.remove();
  });

  it("leaves an ordinary visit alone", async () => {
    window.history.replaceState({}, "", "/");
    vi.stubGlobal("fetch", vi.fn(respondWith(SIGNED_OUT)));
    const { get, root, host } = await mount();
    expect(get().authOpen).toBe(false);
    expect(get().authMode).toBe("login");
    await act(async () => { root.unmount(); });
    host.remove();
  });
});
