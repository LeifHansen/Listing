/* "Teach the AI" on Settings, and the one thing it has to make clear.
 *
 * A reference link changes what the AI writes on a listing, so the seller
 * needs to understand — from the screen, without reading any docs — which
 * half of what they saved is an instruction and which half is not:
 *
 *   - their NOTE is an instruction the AI follows. It is why the link is
 *     there, and a seller who thinks otherwise writes a useless one.
 *   - the PAGE is evidence the AI weighs against the photos, and nothing on
 *     it can change what a listing may claim or what it is priced at. A
 *     seller who expects a linked page to be obeyed will link a page and
 *     then not understand why the draft ignored it.
 *
 * And a reference that stopped working has to SAY so, because the failure is
 * silent by nature: the draft simply comes back as if the link were not
 * there, and the seller goes on believing the AI is reading something it
 * cannot reach.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { SettingsView } from "@/views/SettingsView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "lahey@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/prefs": { prefs: {} },
  "/api/profile": { user: { email: "lahey@example.com", display_name: "" },
                    ebay: { connected: false } },
  "/api/account/summary": { listings: 0, images: 0 },
  "/api/experts": { experts: [{ name: "art" }, { name: "denim" }] },
};

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function server(references, posts = []) {
  return (url, init) => {
    const path = String(url);
    if (path.startsWith("/api/expert-knowledge")) {
      if ((init?.method || "GET") !== "GET") {
        posts.push({ path, method: init.method, body: init.body });
        return json({ ok: true });
      }
      return references();
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
  };
}

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <SettingsView />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, host, text: () => host.textContent || "" };
}

let root;
beforeEach(() => { vi.restoreAllMocks(); });
afterEach(async () => {
  if (root) await act(async () => root.unmount());
  root = null;
  document.body.innerHTML = "";
});

const ONE = {
  references: [{
    id: "r1", expert: "art", url: "https://example.test/dali",
    note: "Use this for Dali edition numbers.",
    distillate: "Dali graphics are catalogued by Michler & Lopsinger numbers.",
    scope: "account", enabled: true, editable: true, fetch_error: "",
  }],
  cap: 20,
};

describe("Teach the AI", () => {
  it("says the note is an instruction and the page is only evidence", async () => {
    vi.stubGlobal("fetch", vi.fn(server(() => json(ONE))));
    const m = await mount();
    root = m.root;
    const text = m.text();
    expect(text).toContain("Your note is an instruction the AI");
    expect(text).toContain("The page itself is only evidence");
    // ...and what that protects, in the seller's terms rather than ours.
    expect(text).toMatch(/claim|priced/i);
  });

  it("shows what the AI actually took from the page, labelled as a summary",
     async () => {
    vi.stubGlobal("fetch", vi.fn(server(() => json(ONE))));
    const m = await mount();
    root = m.root;
    const text = m.text();
    expect(text).toContain("What the AI read from this page");
    expect(text).toContain("Michler & Lopsinger");
    expect(text).toContain("never as instructions");
  });

  it("says so when a reference could not be read", async () => {
    const broken = {
      references: [{
        ...ONE.references[0],
        distillate: "",
        fetch_error: "that page had nothing usable on it — a login wall",
      }],
      cap: 20,
    };
    vi.stubGlobal("fetch", vi.fn(server(() => json(broken))));
    const m = await mount();
    root = m.root;
    expect(m.text()).toContain("a login wall");
  });

  it("gives the seller a way to read the page again when it failed", async () => {
    // Most of the reasons a reference fails are about the moment rather than
    // the page — the site was busy, it asked for consent first, our own
    // summariser was down — and the server has already retried those twice by
    // the time this message is on screen. Without this button the only way to
    // re-read a page was to delete the reference and type it in again.
    const broken = {
      references: [{
        ...ONE.references[0],
        distillate: "",
        fetch_error: "the site is busy or limiting how often it will answer "
                     + "(HTTP 429) — press Try again whenever you like",
      }],
      cap: 20,
    };
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(() => json(broken), posts)));
    const m = await mount();
    root = m.root;

    const button = [...m.host.querySelectorAll("button")]
      .find((b) => (b.textContent || "").includes("Try again"));
    expect(button).toBeTruthy();
    await act(async () => { button.click(); });
    expect(posts.map((p) => p.path))
      .toContain("/api/expert-knowledge/r1/refresh");
  });

  it("does not offer to re-read a built-in reference the seller doesn't own",
     async () => {
    const broken = {
      references: [{
        ...ONE.references[0], scope: "global", editable: false,
        distillate: "", fetch_error: "the site is busy (HTTP 429)",
      }],
      cap: 20,
    };
    vi.stubGlobal("fetch", vi.fn(server(() => json(broken))));
    const m = await mount();
    root = m.root;
    expect(m.text()).toContain("the site is busy");
    expect([...m.host.querySelectorAll("button")]
      .some((b) => (b.textContent || "").includes("Try again"))).toBe(false);
  });

  it("marks a built-in reference as not the seller's to edit", async () => {
    const global = {
      references: [{ ...ONE.references[0], scope: "global", editable: false }],
      cap: 20,
    };
    vi.stubGlobal("fetch", vi.fn(server(() => json(global))));
    const m = await mount();
    root = m.root;
    expect(m.text()).toContain("Built in");
    // No delete control for something the seller does not own.
    const buttons = [...m.host.querySelectorAll("[aria-label]")]
      .map((b) => b.getAttribute("aria-label"));
    expect(buttons).not.toContain("Remove reference");
  });

  it("does not render an empty list as if nothing were wrong when the read failed",
     async () => {
    vi.stubGlobal("fetch", vi.fn(server(() => json({ detail: "boom" }, 500))));
    const m = await mount();
    root = m.root;
    // The same rule the rest of this screen follows: a failed read is never
    // shown as a fact about the account.
    expect(m.text()).not.toContain("No references yet");
  });

  it("says nothing is saved when nothing is saved", async () => {
    vi.stubGlobal("fetch", vi.fn(server(() => json({ references: [], cap: 20 }))));
    const m = await mount();
    root = m.root;
    expect(m.text()).toContain("No references yet");
  });
});
