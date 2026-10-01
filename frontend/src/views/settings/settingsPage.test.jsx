/* The redesigned Settings page, end to end through the real store.
 *
 * What it pins:
 *   - the sections and the jump nav are there, in eBay's order;
 *   - a switch that saves itself PATCHes one key and shows "Saved";
 *   - a failed save snaps the switch back and says why, inline;
 *   - the Best Offer limits appear only when offers are on, refuse an
 *     inverted pair before anything is sent, and save both in one POST;
 *   - unread defaults offer no Save button at all (the smoke test asserts
 *     the same on the built app);
 *   - a deep link opens the "More" row it names and scrolls to it. */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
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
  "/api/easypost/status": { connected: false },
  "/api/account/summary": { listings: 0, images: 0 },
};

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

/** `prefs` answers GET /api/prefs; `write` answers a PATCH/POST to it. */
function server(prefs, writes, write = (body) => json({ ok: true, prefs: body })) {
  return (url, init) => {
    const path = String(url);
    if (path.startsWith("/api/prefs")) {
      if (["POST", "PATCH"].includes(init?.method || "GET")) {
        const body = JSON.parse(init.body);
        writes.push({ method: init.method, body });
        return write(body);
      }
      return prefs();
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
  };
}

function Linker({ section }) {
  const { openSettings } = useApp();
  // The way a real link arrives: from another screen, after the store is up.
  useEffect(() => { openSettings(section); }, [openSettings, section]);
  return null;
}

async function mount({ section } = {}) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          {section ? <Linker section={section} /> : null}
          <SettingsView />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  const tick = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  const label = (text) => [...host.querySelectorAll("label")]
    .find((l) => (l.textContent || "").includes(text));
  const button = (text) => [...host.querySelectorAll("button")]
    .find((b) => (b.textContent || "").trim() === text);
  return {
    root, host, tick,
    text: () => host.textContent || "",
    offers: () => label("Allow offers on new listings")?.querySelector("input[type=checkbox]"),
    input: (aria) => host.querySelector(`input[aria-label="${aria}"]`),
    button,
    buttons: () => [...host.querySelectorAll("button")].map((b) => (b.textContent || "").trim()),
    setValue: async (el, value) => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      await act(async () => {
        setter.call(el, value);
        el.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
  };
}

describe("the Settings page", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("lays the sections out in eBay's order with a jump nav", async () => {
    vi.stubGlobal("fetch", vi.fn(server(() => json({ prefs: {} }), [])));
    const s = await mount();
    const nav = s.host.querySelector('nav[aria-label="Settings sections"]');
    expect([...nav.querySelectorAll("button")].map((b) => b.textContent)).toEqual([
      "eBay account", "Shipping", "Returns & payment", "Selling defaults", "More", "Danger zone",
    ]);
    for (const id of ["ebay-account", "shipping", "returns-payment", "selling-defaults", "more", "danger"]) {
      expect(s.host.querySelector(`#settings-${id}`), id).toBeTruthy();
    }
    // No page-wide Save: every control answers for itself.
    expect(s.buttons()).not.toContain("Save defaults");
    await act(async () => { s.root.unmount(); });
  });

  it("saves a switch the moment it changes, one key by PATCH, and says so", async () => {
    const writes = [];
    vi.stubGlobal("fetch", vi.fn(server(() => json({ prefs: {} }), writes)));
    const s = await mount();
    await act(async () => { s.offers().click(); });
    await s.tick();
    expect(writes).toEqual([{ method: "PATCH", body: { allow_offers: 1 } }]);
    expect(s.offers().checked).toBe(true);
    expect(s.text()).toContain("Saved");
    await act(async () => { s.root.unmount(); });
  });

  it("snaps a switch back when the save fails, and says why inline", async () => {
    const writes = [];
    vi.stubGlobal("fetch", vi.fn(server(
      () => json({ prefs: { allow_offers: 0 } }), writes,
      () => json({ detail: "We couldn’t save your defaults just now." }, 503))));
    const s = await mount();
    await act(async () => { s.offers().click(); });
    await s.tick();
    expect(s.offers().checked).toBe(false);
    expect(s.text()).toContain("Couldn’t save");
    expect(s.text()).toContain("We couldn’t save your defaults just now.");
    await act(async () => { s.root.unmount(); });
  });

  it("offers the Best Offer limits only when offers are on, and refuses an inverted pair", async () => {
    const writes = [];
    vi.stubGlobal("fetch", vi.fn(server(() => json({ prefs: { allow_offers: 1 } }), writes)));
    const s = await mount();
    const accept = s.input("Auto-accept offers at this percentage of the price");
    const decline = s.input("Auto-decline offers below this percentage of the price");
    expect(accept && decline).toBeTruthy();
    expect(s.text()).toContain("no minimum");

    await s.setValue(accept, "60");
    await s.setValue(decline, "90");
    expect(s.text()).toContain("below auto-accept");
    expect(s.button("Save offer limits").disabled).toBe(true);
    expect(writes).toEqual([]);

    await s.setValue(decline, "40");
    expect(s.text()).toContain("$30.00 and up is accepted for you");
    expect(s.text()).toContain("under $20.00 is declined for you");
    await act(async () => { s.button("Save offer limits").click(); });
    await s.tick();
    expect(writes.at(-1)).toEqual({
      method: "POST",
      body: { best_offer_auto_accept_pct: 60, best_offer_auto_decline_pct: 40 },
    });
    await act(async () => { s.root.unmount(); });
  });

  it("hides the limits while offers are off", async () => {
    vi.stubGlobal("fetch", vi.fn(server(() => json({ prefs: { allow_offers: 0 } }), [])));
    const s = await mount();
    expect(s.input("Auto-accept offers at this percentage of the price")).toBeNull();
    await act(async () => { s.root.unmount(); });
  });

  it("offers no Save at all for defaults that could not be read", async () => {
    vi.stubGlobal("fetch", vi.fn(server(
      () => json({ detail: "We couldn’t read your saved settings." }, 503), [])));
    const s = await mount();
    expect(s.text()).toContain("couldn’t load your saved defaults");
    expect(s.buttons().filter((b) => /^Save (offer limits|ad rate|quantity)$/.test(b))).toEqual([]);
    expect(s.offers()).toBeUndefined();
    expect(s.buttons().filter((b) => b === "Try again").length).toBeGreaterThan(0);
    await act(async () => { s.root.unmount(); });
  });

  it("opens the More row a deep link names and scrolls to it", async () => {
    vi.stubGlobal("fetch", vi.fn(server(() => json({ prefs: {} }), [])));
    const scrolled = [];
    Element.prototype.scrollIntoView = function scrollIntoView() { scrolled.push(this.id); };
    const s = await mount({ section: "easypost" });
    await act(async () => { await new Promise((r) => requestAnimationFrame(() => r())); });
    await s.tick();
    const row = [...s.host.querySelectorAll("button[aria-expanded]")]
      .find((b) => (b.textContent || "").includes("Shipping labels"));
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(s.text()).toContain("EasyPost API key");
    expect(scrolled).toContain("settings-easypost");
    await act(async () => { s.root.unmount(); });
  });
});
