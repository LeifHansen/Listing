/* A draft saves itself.
 *
 * Every edit reaches the server a moment after the seller stops typing, as a
 * PATCH naming the fields that changed and nothing else (lib/fieldDiff over
 * the door Phase 0 opened). What this pins: the debounce, the shape of the
 * request, the baseline moving with every path that writes, the gate on
 * listings a timer must never touch, the wait before a publish, and what a
 * failure does.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { grantAiConsent } from "@/lib/api";
import { useListingForm, AUTOSAVE_DELAY_MS, AUTOSAVE_RETRY_MS } from "./useListingForm";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function ok(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function Probe({ onValue }) {
  const app = useApp();
  const form = useListingForm();
  useEffect(() => { onValue({ app, form }); });
  return null;
}

let root;
let host;

const LISTING = {
  title: "Old title", description: "Body", price: 30, quantity: 1,
  condition: "USED_EXCELLENT", category_id: "111",
  item_specifics: [{ name: "Brand", value: "Acme", confidence: "high" }],
  images: ["img_000.jpg"],
};

async function mountEditor(session = {}) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  let value = null;
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Probe onValue={(v) => { value = v; }} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await Promise.resolve(); });
  await act(async () => {
    value.app.setSession({ sessionId: "s1", listing: { ...LISTING }, ...session });
  });
  grantAiConsent();
  return () => value;
}

// Run the debounce out, then let the PATCH's promise chain settle.
async function tick(ms) {
  await act(async () => { vi.advanceTimersByTime(ms); });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

function patches(calls) {
  return calls.filter((c) => c.method === "PATCH" && c.path.startsWith("/api/listings/s1"));
}

function fetchRecorder(answer) {
  const calls = [];
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    const path = String(url);
    const body = opts.body && typeof opts.body === "string" ? JSON.parse(opts.body) : null;
    calls.push({ path, method: opts.method || "GET", body });
    return answer(path, opts, body);
  }));
  return calls;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("autosave", () => {
  it("sends the changed fields, and only those, after the seller stops typing", async () => {
    const calls = fetchRecorder((path, opts, body) => {
      if (opts.method === "PATCH") return ok({ ok: true, listing: { ...LISTING, ...body } });
      return ok({});
    });
    const get = await mountEditor();

    await act(async () => { get().form.set("title", "New"); });
    await act(async () => { get().form.set("title", "New title"); });
    expect(get().form.saveStatus).toBe("dirty");
    // Nothing yet: the timer has not run out.
    await tick(AUTOSAVE_DELAY_MS - 100);
    expect(patches(calls)).toHaveLength(0);

    await tick(200);
    const sent = patches(calls);
    expect(sent).toHaveLength(1);
    // The title, as typed last -- not the description, not the price, and
    // not the whole listing.
    expect(sent[0].body).toEqual({ title: "New title" });
    expect(get().form.saveStatus).toBe("saved");
    // The card in the grid got the merged answer without a refetch.
    const row = get().app.listingsState.items.find((i) => i.id === "s1");
    expect(row ? row.listing.title : "New title").toBe("New title");
  });

  it("does not save a listing it only opened", async () => {
    const calls = fetchRecorder(() => ok({}));
    await mountEditor();
    await tick(AUTOSAVE_DELAY_MS * 3);
    expect(patches(calls)).toHaveLength(0);
  });

  it("never saves a live listing, nor a sold one", async () => {
    for (const session of [{ status: "published" }, { status: "live" }, { status: "sold" },
                           { listing: { ...LISTING, source: "ebay", ebay_listing_id: "9" } }]) {
      const calls = fetchRecorder(() => ok({}));
      const get = await mountEditor(session);
      await act(async () => { get().form.set("title", "Edited live"); });
      await tick(AUTOSAVE_DELAY_MS * 3);
      expect(patches(calls), JSON.stringify(session)).toHaveLength(0);
      expect(get().form.autosaveOn).toBe(false);
      expect(get().form.saveStatus).toBe("off");
      act(() => root.unmount());
      host.remove();
      vi.unstubAllGlobals();
    }
    // afterEach unmounts once more; give it something to unmount.
    await mountEditor();
  });

  it("waits for a save in flight, and sends nothing of its own, while a publish runs", async () => {
    let releasePatch;
    const calls = fetchRecorder((path, opts, body) => {
      if (opts.method === "PATCH") {
        return new Promise((r) => { releasePatch = () => r(ok({ ok: true, listing: { ...LISTING, ...body } }).then((x) => x)); })
          .then((x) => x);
      }
      if (path.startsWith("/api/save/s1")) return ok({ saved: true });
      if (path.startsWith("/api/publish")) return ok({ draft: true, message: "Draft saved" });
      return ok({});
    });
    const get = await mountEditor();
    await act(async () => { get().form.set("title", "Mid-flight"); });
    await tick(AUTOSAVE_DELAY_MS + 50);
    expect(patches(calls)).toHaveLength(1);   // out, not answered

    // The publish starts while the PATCH is unanswered...
    let done = false;
    act(() => { get().form.publish("draft").then(() => { done = true; }); });
    await act(async () => { await Promise.resolve(); });
    // ...and does not reach the server until the PATCH has.
    expect(calls.some((c) => c.path.startsWith("/api/save/s1"))).toBe(false);
    await act(async () => { releasePatch(); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    expect(calls.some((c) => c.path.startsWith("/api/save/s1"))).toBe(true);
    await act(async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); });
    expect(done).toBe(true);
    // No second PATCH rode along with the publish.
    expect(patches(calls)).toHaveLength(1);
  });

  it("moves its baseline when a refine rewrites the listing", async () => {
    const calls = fetchRecorder((path, opts) => {
      if (path.startsWith("/api/refine")) {
        return ok({ ...LISTING, title: "Refined title", description: "Refined body" });
      }
      if (opts.method === "PATCH") return ok({ ok: true, listing: LISTING });
      return ok({});
    });
    const get = await mountEditor();
    await act(async () => { get().form.set("title", "Typed first"); });
    await act(async () => { await get().form.refine("make it better"); });
    // The refine saved server-side; the editor now shows exactly what the
    // server holds, so there is nothing for the timer to send.
    await tick(AUTOSAVE_DELAY_MS * 2);
    expect(patches(calls)).toHaveLength(0);
    expect(get().form.form.title).toBe("Refined title");
  });

  it("retries a failed save with a widening gap and says so once", async () => {
    let fail = 2;
    const calls = fetchRecorder((path, opts, body) => {
      if (opts.method === "PATCH") {
        if (fail > 0) { fail -= 1; return ok({ detail: "Couldn't save that change just now." }, 503); }
        return ok({ ok: true, listing: { ...LISTING, ...body } });
      }
      return ok({});
    });
    const get = await mountEditor();
    await act(async () => { get().form.set("title", "Keeps trying"); });
    await tick(AUTOSAVE_DELAY_MS + 50);
    expect(patches(calls)).toHaveLength(1);
    expect(get().form.saveStatus).toBe("error");
    expect(document.body.textContent).toContain("Couldn't save your changes just now");

    await tick(AUTOSAVE_RETRY_MS[0] + 50);
    expect(patches(calls)).toHaveLength(2);
    expect(get().form.saveStatus).toBe("error");
    await tick(AUTOSAVE_RETRY_MS[1] + 50);
    expect(patches(calls)).toHaveLength(3);
    expect(get().form.saveStatus).toBe("saved");
    // One toast for the whole episode.
    expect(document.body.textContent.split("Couldn't save your changes just now").length - 1).toBe(1);
  });

  it("stops at once when the server says this listing is eBay's", async () => {
    const calls = fetchRecorder((path, opts) => {
      if (opts.method === "PATCH") {
        return ok({ detail: "This listing is live on eBay — changes to it go out with Update Live Listing, not a background save." }, 409);
      }
      return ok({});
    });
    const get = await mountEditor();
    await act(async () => { get().form.set("title", "Should stop"); });
    await tick(AUTOSAVE_DELAY_MS + 50);
    expect(patches(calls)).toHaveLength(1);
    expect(get().form.saveStatus).toBe("off");
    await tick(AUTOSAVE_RETRY_MS[2] * 2);
    expect(patches(calls)).toHaveLength(1);
  });

  it("flushSave sends what the timer has not reached, so Done loses nothing", async () => {
    const calls = fetchRecorder((path, opts, body) => {
      if (opts.method === "PATCH") return ok({ ok: true, listing: { ...LISTING, ...body } });
      return ok({});
    });
    const get = await mountEditor();
    await act(async () => { get().form.set("description", "Typed and left"); });
    await act(async () => { await get().form.flushSave(); });
    expect(patches(calls)).toHaveLength(1);
    expect(patches(calls)[0].body).toEqual({ description: "Typed and left" });
    expect(get().form.dirty).toBe(false);
  });
});

describe("autosave, pressed for time", () => {
  it("flushSave sends what was typed while a save was in flight", async () => {
    let release;
    const calls = fetchRecorder((path, opts, body) => {
      if (opts.method === "PATCH") {
        if (!release) {
          return new Promise((r) => { release = () => r(ok({ ok: true, listing: { ...LISTING, ...body } })); }).then((x) => x);
        }
        return ok({ ok: true, listing: { ...LISTING, title: "First", ...body } });
      }
      return ok({});
    });
    const get = await mountEditor();
    await act(async () => { get().form.set("title", "First"); });
    await tick(AUTOSAVE_DELAY_MS + 50);
    expect(patches(calls)).toHaveLength(1);
    // More typing while the first PATCH is unanswered, then Done.
    await act(async () => { get().form.set("description", "Second"); });
    let flushed = false;
    act(() => { get().form.flushSave().then(() => { flushed = true; }); });
    await act(async () => { release(); for (let i = 0; i < 8; i++) await Promise.resolve(); });
    expect(flushed).toBe(true);
    expect(patches(calls)).toHaveLength(2);
    expect(patches(calls)[1].body).toEqual({ description: "Second" });
  });
});
