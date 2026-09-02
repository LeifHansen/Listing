/* Arriving from the marketing site's "Log in" or "Sign up" has to land in
 * that form.
 *
 * The marketing site splits one "Open the app" button into Log in and Sign up,
 * and the only thing carrying that intent across the two origins is
 * `?login=1` / `?signup=1`. If the app ignores it, each button is a lie: Log
 * in drops a returning seller on a signed-out dashboard with the sign-in
 * prompt closed, and Sign up drops a would-be customer on the same — or,
 * worse, on the Log in tab, asking them for a password they have never set.
 *
 * The two failure modes worth pinning are the ones that only show up with
 * timing. A seller who is ALREADY signed in and follows the same link (a
 * bookmark, a shared URL) must never get a sign-in box thrown over their own
 * dashboard, so the dialog waits for /api/auth/me to answer rather than firing
 * on mount. And the param must not survive in the URL, or a refresh reopens it
 * forever.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp, authIntentFromSearch } from "@/store";
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

/* The Log in button used to be a bare link to the app, which put a signed-out
 * visitor on the dashboard with nothing open: they asked to sign in and were
 * handed a page with a sign-in button on it to find. It carries `?login=1` now
 * and gets the same treatment as Sign up, on the other tab. */
describe("?login=1 from the marketing site", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState({}, "", "/?login=1");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

  it("opens the dialog on the Log in tab for a signed-out visitor", async () => {
    vi.stubGlobal("fetch", vi.fn(respondWith(SIGNED_OUT)));
    const { get, root, host } = await mount();
    expect(get().authOpen).toBe(true);
    expect(get().authMode).toBe("login");
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

  it("sends someone already signed in straight to their dashboard", async () => {
    vi.stubGlobal("fetch", vi.fn(respondWith(SIGNED_IN)));
    const { get, root, host } = await mount();
    expect(get().user).toBeTruthy();
    expect(get().authOpen).toBe(false);
    expect(window.location.search).toBe("");
    await act(async () => { root.unmount(); });
    host.remove();
  });
});

describe("authIntentFromSearch", () => {
  it("reads the two marketing-site params and nothing else", () => {
    expect(authIntentFromSearch("?login=1")).toBe("login");
    expect(authIntentFromSearch("?signup=1")).toBe("signup");
    expect(authIntentFromSearch("")).toBeNull();
    expect(authIntentFromSearch("?ebay=connected")).toBeNull();
    // Only the exact value the site sends counts; "?login=" or "?login=0" is
    // not a request to open anything.
    expect(authIntentFromSearch("?login=0")).toBeNull();
    expect(authIntentFromSearch("?login=")).toBeNull();
  });

  it("survives the app's own params riding along", () => {
    expect(authIntentFromSearch("?utm_source=x&login=1")).toBe("login");
  });
});
