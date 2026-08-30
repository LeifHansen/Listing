/* The buyer-message slice: what it fetches, what it counts, and when it stays
 * quiet.
 *
 * Two properties matter more than the rest. With messaging switched off the
 * app must make NO request at all — the flag is default-off because eBay's
 * message scope is limited-release, and a deployment that hasn't been approved
 * shouldn't be paying for a poll it can't use. And the unread badge has to
 * drop the instant a conversation is opened, because a badge that lags feels
 * broken even when the server agrees a moment later.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const CONVERSATIONS = [
  { id: "ebay:c1", raw_id: "c1", marketplace: "ebay", marketplace_label: "eBay",
    counterparty: "sarah_m", snippet: "Is the lens still available?",
    last_at: "2026-08-30T09:00:00Z", unread: 2 },
  { id: "etsy:c9", raw_id: "c9", marketplace: "etsy", marketplace_label: "Etsy",
    counterparty: "dave", snippet: "Thanks!", last_at: "2026-08-29T09:00:00Z",
    unread: 1 },
];

function routes({ messaging = true } = {}) {
  return {
    "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
    "/api/health": { anthropic_configured: true, ebay_configured: false },
    "/api/ebay/status": {
      connected: true, oauth_ready: true, username: "my_shop",
      messaging_enabled: messaging,
    },
    "/api/listings": { authed: true, db: {}, listings: [] },
    "/api/notifications": { notifications: [], unread: 0 },
    "/api/marketplaces": { marketplaces: [] },
    "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
    "/api/messages/read": { ok: true },
    "/api/messages": {
      conversations: CONVERSATIONS, unread: 3, available: true, reason: "",
      sources: [
        { key: "ebay", label: "eBay", available: true, unread: 2, supported: true },
        { key: "etsy", label: "Etsy", available: true, unread: 1, supported: true },
      ],
    },
  };
}

let TABLE = routes();

function respond(path) {
  // Longest match wins, so "/api/messages/read" isn't shadowed by "/api/messages".
  const key = Object.keys(TABLE)
    .filter((k) => path.startsWith(k))
    .sort((a, b) => b.length - a.length)[0];
  const body = key ? TABLE[key] : null;
  return Promise.resolve({
    ok: !!body,
    status: body ? 200 : 404,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body || { detail: "Not found" }),
    text: () => Promise.resolve(JSON.stringify(body || { detail: "Not found" })),
  });
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

describe("buyer messages", () => {
  beforeEach(() => {
    TABLE = routes();
    vi.stubGlobal("fetch", vi.fn((url) => respond(String(url))));
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("loads conversations from every marketplace with one global unread count",
    async () => {
      const { get, root } = await mount();
      expect(get().messages.conversations).toHaveLength(2);
      expect(get().messages.unread).toBe(3);
      expect(get().messages.sources.map((s) => s.key)).toEqual(["ebay", "etsy"]);
      await act(async () => { root.unmount(); });
    });

  it("makes no request at all when messaging is switched off", async () => {
    TABLE = routes({ messaging: false });
    const { get, root } = await mount();
    const asked = fetch.mock.calls.map((c) => String(c[0]));
    expect(asked.some((u) => u.startsWith("/api/messages"))).toBe(false);
    expect(get().messages.unread).toBe(0);
    expect(get().messages.loaded).toBe(false);
    await act(async () => { root.unmount(); });
  });

  it("drops the badge the moment a conversation is opened", async () => {
    const { get, root } = await mount();
    expect(get().messages.unread).toBe(3);

    // Freeze the network so nothing can answer: whatever the badge reads after
    // this is the optimistic update, not a refetch.
    fetch.mockImplementation(() => new Promise(() => {}));
    await act(async () => { get().openConversation("ebay:c1"); });

    expect(get().messages.unread).toBe(1);
    expect(get().messages.conversations.find((c) => c.id === "ebay:c1").unread)
      .toBe(0);
    expect(get().activeConversationId).toBe("ebay:c1");
    await act(async () => { root.unmount(); });
  });

  it("opening the same conversation twice doesn't double-subtract", async () => {
    const { get, root } = await mount();
    fetch.mockImplementation(() => new Promise(() => {}));
    await act(async () => { get().openConversation("ebay:c1"); });
    await act(async () => { get().openConversation("ebay:c1"); });
    expect(get().messages.unread).toBe(1);
    await act(async () => { root.unmount(); });
  });

  it("jumping to a conversation switches to the Messages screen", async () => {
    const { get, root } = await mount();
    await act(async () => { get().openMessages("etsy:c9"); });
    expect(get().view).toBe("messages");
    expect(get().activeConversationId).toBe("etsy:c9");
    await act(async () => { root.unmount(); });
  });

  it("keeps what it has when a poll fails, rather than blanking the inbox",
    async () => {
      const { get, root } = await mount();
      expect(get().messages.conversations).toHaveLength(2);
      fetch.mockImplementation(() => Promise.reject(new Error("offline")));
      await act(async () => { await get().loadMessages(); });
      expect(get().messages.conversations).toHaveLength(2);
      await act(async () => { root.unmount(); });
    });

  it("marks a failed reply instead of losing what was typed", async () => {
    const { get, root } = await mount();
    await act(async () => { get().openConversation("ebay:c1"); });
    fetch.mockImplementation(() => Promise.reject(new Error("nope")));
    await act(async () => {
      const ok = await get().sendMessage("ebay:c1", "Yes, still available!");
      expect(ok).toBe(false);
    });
    const sent = get().threads["ebay:c1"].messages.at(-1);
    expect(sent.text).toBe("Yes, still available!");
    expect(sent.failed).toBe(true);
    await act(async () => { root.unmount(); });
  });
});
