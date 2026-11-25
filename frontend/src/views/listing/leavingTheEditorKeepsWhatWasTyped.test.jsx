/* Leaving the editor does not throw away the last few keystrokes.
 *
 * Autosave sends an edit AUTOSAVE_DELAY_MS after the seller stops typing,
 * and only Done, Back and the menu's exits waited for it. Every other way
 * out -- "Back to batch", a tap on the List or Manage tab, another screen,
 * a listing opened from the bell -- unmounted the editor or re-seeded it,
 * the debounce's cleanup cancelled the timer, and the title fixed a second
 * earlier was simply gone. And a save that was failing kept retrying from
 * an editor that no longer existed, every fifteen seconds until a reload.
 */
import { act, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
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

const LISTING = {
  title: "Old title", price: 30, quantity: 1, condition: "USED_EXCELLENT",
  category_id: "111", images: ["img_000.jpg"], videos: [],
};

// The editor under a shell that can take it away, the way the app's
// screens do, while the store stays put -- and that, like NewListing, only
// draws it while a listing is open.
function Editor({ onValue }) {
  const form = useListingForm();
  useEffect(() => { onValue(form); });
  return null;
}
function Shell({ expose }) {
  const app = useApp();
  const [shown, setShown] = useState(true);
  useEffect(() => { expose({ app, setShown }); }, [expose, app]);
  return shown && app.session ? <Editor onValue={(form) => expose({ form })} /> : null;
}

let root;
let host;
let calls;

async function mount(answer) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    const path = String(url);
    const body = typeof opts.body === "string" ? JSON.parse(opts.body) : null;
    calls.push({ path, method: opts.method || "GET", body });
    return answer(path, opts, body);
  }));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const v = {};
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Shell expose={(x) => Object.assign(v, x)} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await Promise.resolve(); });
  await act(async () => {
    v.app.setSession({ sessionId: "s1", status: "draft", listing: { ...LISTING } });
  });
  return v;
}

async function tick(ms) {
  await act(async () => { vi.advanceTimersByTime(ms); });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

const patchesTo = (id) => calls.filter(
  (c) => c.method === "PATCH" && c.path === `/api/listings/${id}`);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("leaving the editor", () => {
  it("sends an edit typed just before the editor goes away", async () => {
    const v = await mount((path, opts, body) => (opts.method === "PATCH"
      ? ok({ ok: true, listing: { ...LISTING, ...body } }) : ok({})));

    await act(async () => { v.form.set("title", "Fixed title"); });
    await tick(AUTOSAVE_DELAY_MS / 5);
    expect(patchesTo("s1")).toHaveLength(0);

    await act(async () => { v.setShown(false); });
    await tick(0);
    const sent = patchesTo("s1");
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toEqual({ title: "Fixed title" });
  });

  it("sends it to the listing it was typed into when another one opens", async () => {
    const v = await mount((path, opts, body) => (opts.method === "PATCH"
      ? ok({ ok: true, listing: { ...LISTING, ...body } }) : ok({})));

    await act(async () => { v.form.set("title", "Fixed title"); });
    await act(async () => {
      v.app.setSession({ sessionId: "s2", status: "draft",
                         listing: { ...LISTING, title: "Another listing" } });
    });
    await tick(AUTOSAVE_DELAY_MS * 2);

    expect(patchesTo("s1").map((c) => c.body)).toEqual([{ title: "Fixed title" }]);
    expect(patchesTo("s2")).toHaveLength(0);
    // The listing now open is not told about the other one's save.
    expect(v.form.form.title).toBe("Another listing");
    expect(v.form.saveStatus).toBe("idle");
  });

  it("does not send an edit a draft save has just saved, on the way out", async () => {
    const v = await mount((path, opts, body) => (opts.method === "PATCH"
      ? ok({ ok: true, listing: { ...LISTING, ...body } }) : ok({})));

    await act(async () => { v.form.set("title", "Fixed title"); });
    // Save to eBay drafts: a full save of everything, then the editor closes.
    await act(async () => { v.form.publish("draft"); });
    await tick(0);
    await tick(0);
    expect(calls.some((c) => c.path === "/api/save/s1")).toBe(true);
    expect(v.app.session).toBe(null);
    await tick(AUTOSAVE_DELAY_MS * 2);
    expect(patchesTo("s1")).toHaveLength(0);
  });

  it("stops retrying a failing save once the editor is gone", async () => {
    const v = await mount((path, opts) => (opts.method === "PATCH"
      ? ok({ detail: "Server unavailable" }, 503) : ok({})));

    await act(async () => { v.form.set("title", "Fixed title"); });
    await tick(AUTOSAVE_DELAY_MS + 10);
    expect(patchesTo("s1")).toHaveLength(1);
    expect(v.form.saveStatus).toBe("error");

    await act(async () => { v.setShown(false); });
    await tick(0);
    const atLeave = patchesTo("s1").length;
    // Well past every retry gap, several times over.
    for (let i = 0; i < 5; i += 1) await tick(AUTOSAVE_RETRY_MS.at(-1) + 1000);
    expect(patchesTo("s1")).toHaveLength(atLeave);
  });

  it("sends nothing after the seller chose to discard", async () => {
    // A draft whose save is failing asks "Close without saving? ... changes
    // since the last save are discarded", and Close is the seller agreeing.
    // The exit flush must not then send them anyway once the server is back.
    let down = true;
    const v = await mount((path, opts, body) => (opts.method === "PATCH"
      ? (down ? ok({ detail: "Server unavailable" }, 503)
              : ok({ ok: true, listing: { ...LISTING, ...body } }))
      : ok({})));

    await act(async () => { v.form.set("title", "Fixed title"); });
    await tick(AUTOSAVE_DELAY_MS + 10);
    expect(v.form.saveStatus).toBe("error");
    const before = patchesTo("s1").length;

    down = false;
    await act(async () => { v.form.discardUnsaved(); v.setShown(false); });
    for (let i = 0; i < 3; i += 1) await tick(AUTOSAVE_RETRY_MS.at(-1) + 1000);

    expect(patchesTo("s1")).toHaveLength(before);
  });
});
